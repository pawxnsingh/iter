from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.FileReadTool.prompt import READ_PROMPT
from utils.paths import is_binary, resolve_path
from utils.text import count_tokens


class ReadFileParams(BaseModel):
    file_path: str = Field(
        ...,
        description="The absolute path to the file to read.",
    )
    offset: int = Field(
        1,
        ge=0,
        description="The line number to start reading from. Only provide if the file is too large to read at once.",
    )
    limit: int = Field(
        None,
        ge=1,
        description="The maximum number of lines to read from the offset. If None, reads until end of file.",
    )
    pages: str | None = Field(
        None,
        description='Page range for PDF files (e.g., "1-5", "3", "10-20"). Only applicable to PDF files. Maximum 20 pages per request.',
    )


class FileReadTool(Tool):
    name = "Read"
    description = "Reads a file from the local filesystem. You can access any file directly by using this tool."
    prompt = READ_PROMPT
    schema = ReadFileParams
    kind = ToolKind.READ
    max_result_size_chars = float("inf")

    MAX_FILE_SIZE = 1024 * 1024 * 10
    MAX_OUTPUT_TOKENS = 25000

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    def get_path(self, params: dict) -> str | None:
        return params.get("file_path")

    def get_permission_value(self, params: dict) -> str | None:
        """Path matched against `Read(<pattern>)` rules."""
        return params.get("file_path")

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ReadFileParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.file_path)

        if not path.exists():
            return ToolResult.error_result(
                error=f"File does not exist. Note: your current working directory "
                f"is {invocation.cwd}."
            )

        if not path.is_file():
            return ToolResult.error_result(error=f"Path is not a file: {path}")

        file_size = path.stat().st_size

        if file_size > self.MAX_FILE_SIZE:
            return ToolResult.error_result(
                error=f"File too Large {file_size / (1024 * 1024):.1f}MB",
                output=f"Max File Size is {self.MAX_FILE_SIZE:.0f}MB",
            )

        if path.suffix == ".ipynb":
            return self._read_notebook(path)

        _IMAGE_TYPES = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        media_type = _IMAGE_TYPES.get(path.suffix.lower())
        if media_type:
            return self._read_image(path, media_type)

        if path.suffix.lower() == ".pdf":
            return self._read_pdf(path, params.pages)

        if is_binary(path=path):
            ext = path.suffix or "unknown"
            return ToolResult.error_result(
                error=f"This tool cannot read binary files. The file appears to be "
                f"a binary {ext} file. Please use appropriate tools for binary "
                "file analysis.",
            )

        try:
            try:
                read_file = path.read_text("utf-8")
            except UnicodeDecodeError:
                read_file = path.read_text("latin-1")


            import re as _re
            lines = _re.split(r"\r?\n", read_file) if read_file else []
            total_lines = len(lines)

            from tools.builtin.shared.readFileState import record_read
            record_read(
                path, read_file,
                offset=params.offset if params.offset > 1 else None,
                limit=params.limit,
            )

            if total_lines == 0:
                return ToolResult.success_result(
                    output="<system-reminder>Warning: the file exists but the "
                    "contents are empty.</system-reminder>",
                    metadata={
                        "lines": 0,
                    },
                )

            start_idx = max(0, params.offset - 1)

            if params.limit is not None:
                end_idx = min(start_idx + params.limit, total_lines)
            else:
                end_idx = total_lines

            selected_lines = lines[start_idx:end_idx]

            if not selected_lines:
                return ToolResult.success_result(
                    output="<system-reminder>Warning: the file exists but is "
                    f"shorter than the provided offset ({params.offset}). The "
                    f"file has {total_lines} lines.</system-reminder>",
                    metadata={"path": str(path), "total_lines": total_lines},
                )

            formatted_lines = []

            for i, line in enumerate(selected_lines, start=start_idx + 1):
                formatted_lines.append(f"{i}\t{line}")

            output = "\n".join(formatted_lines)

            token_count = count_tokens(text=output)
            if token_count > self.MAX_OUTPUT_TOKENS:
                return ToolResult.error_result(
                    f"File content ({token_count} tokens) exceeds maximum allowed "
                    f"tokens ({self.MAX_OUTPUT_TOKENS}). Use offset and limit "
                    "parameters to read specific portions of the file, or search "
                    "for specific content instead of reading the whole file."
                )

            return ToolResult.success_result(
                output=output,
                metadata={
                    "path": str(path),
                    "total_lines": total_lines,
                    "shown_start": start_idx + 1,
                    "shown_end": end_idx,
                },
            )

        except Exception as e:
            return ToolResult.error_result(
                f"Failed to read files: {e}",
            )

    def _read_pdf(self, path, pages: str | None) -> ToolResult:
        """Read a PDF as VISION.

        PDF text is never extracted — pages are rendered to JPEG images via
        pdftoppm and fed to the model as image blocks (the only approach valid
        for image-input providers):

          - `pages` given  -> render exactly that 1-indexed range (<= 20 pages).
          - no `pages`     -> a whole-PDF read is allowed only for small PDFs
                              (<= PDF_AT_MENTION_INLINE_THRESHOLD pages); larger
                              ones must be read in ranges via `pages`.
        """
        from tools.builtin.shared.pdf import (
            PDF_AT_MENTION_INLINE_THRESHOLD,
            PDF_MAX_PAGES_PER_READ,
            PDFRenderError,
            get_pdf_page_count,
            page_range_count,
            parse_pdf_page_range,
            render_pdf_pages,
        )

        first_page: int | None = None
        last_page: float | None = None

        if pages:
            parsed = parse_pdf_page_range(pages)
            if parsed is None:
                return ToolResult.error_result(
                    f'Invalid pages parameter: "{pages}". Use formats like '
                    '"1-5", "3", or "10-20". Pages are 1-indexed.'
                )
            first_page, last_page = parsed
            if page_range_count(first_page, last_page) > PDF_MAX_PAGES_PER_READ:
                return ToolResult.error_result(
                    f'Page range "{pages}" exceeds maximum of '
                    f"{PDF_MAX_PAGES_PER_READ} pages per request. Please use a "
                    "smaller range."
                )
        else:
            count = get_pdf_page_count(path)
            if count is not None and count > PDF_AT_MENTION_INLINE_THRESHOLD:
                return ToolResult.error_result(
                    f"This PDF has {count} pages, which is too many to read at "
                    'once. Use the pages parameter to read specific page ranges '
                    f'(e.g., pages: "1-5"). Maximum {PDF_MAX_PAGES_PER_READ} '
                    "pages per request."
                )

        try:
            images = render_pdf_pages(path, first_page, last_page)
        except PDFRenderError as e:
            return ToolResult.error_result(str(e))

        scope = f"pages {pages}" if pages else "all pages"
        return ToolResult.success_result(
            output=f"[PDF: {path.name} — rendered {len(images)} page(s) ({scope})]",
            images=images,
            metadata={"path": str(path), "pdf": True, "pages_read": len(images)},
        )

    def _read_image(self, path, media_type: str) -> ToolResult:
        """Return an image as a base64 attachment."""
        import base64

        try:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception as e:
            return ToolResult.error_result(f"Failed to read image: {e}")
        return ToolResult.success_result(
            output=f"[Image: {path.name} ({media_type}, {path.stat().st_size} bytes)]",
            images=[{"media_type": media_type, "data": data}],
            metadata={"path": str(path), "image": True, "media_type": media_type},
        )

    def _read_notebook(self, path) -> ToolResult:
        """Render a.ipynb as readable cells with outputs (parity:
        mapNotebookCellsToToolResult). Each cell shows its index, type, source,
        and a compact form of any text/stream/error outputs."""
        import json as _json

        try:
            raw = path.read_text(encoding="utf-8")
            nb = _json.loads(raw)
        except Exception as e:
            return ToolResult.error_result(f"Failed to parse notebook: {e}")

        from tools.builtin.shared.readFileState import record_read
        record_read(path, raw)

        cells = nb.get("cells", [])
        parts: list[str] = []
        for idx, cell in enumerate(cells):
            ctype = cell.get("cell_type", "code")
            src = cell.get("source", "")
            if isinstance(src, list):
                src = "".join(src)
            cid = cell.get("id", idx)
            parts.append(f"=== Cell [{idx}] id={cid} ({ctype}) ===")
            parts.append(src.rstrip("\n"))

            for out in cell.get("outputs", []) or []:
                otype = out.get("output_type", "")
                if otype == "stream":
                    text = out.get("text", "")
                    if isinstance(text, list):
                        text = "".join(text)
                    parts.append(f"  [stream:{out.get('name','stdout')}] {text.rstrip()}")
                elif otype in ("execute_result", "display_data"):
                    data = out.get("data", {})
                    text = data.get("text/plain", "")
                    if isinstance(text, list):
                        text = "".join(text)
                    if text:
                        parts.append(f"  [out] {text.rstrip()}")
                elif otype == "error":
                    ename = out.get("ename", "Error")
                    evalue = out.get("evalue", "")
                    parts.append(f"  [error] {ename}: {evalue}")

        output = "\n".join(parts) if parts else "Notebook has no cells."
        return ToolResult.success_result(
            output=output,
            metadata={"path": str(path), "cells": len(cells), "notebook": True},
        )
