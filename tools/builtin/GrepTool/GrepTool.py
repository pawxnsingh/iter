"""
Grep tool — ripgrep wrapper, parity.

Source: Shells out to `rg` and
builds args exactly as does: output modes (content / files_with_matches /
count), context lines (-A/-B/-C), -n, -i, glob/type filters, multiline,
head_limit + offset post-processing, --max-columns 500, VCS-dir exclusion.

Falls back to a pure-Python scan only if `rg` is unavailable, covering the
common content/files/count modes so the tool still works on a box without
ripgrep.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.GrepTool.prompt import GREP_PROMPT
from utils.paths import display_path_rel_to_cwd, is_binary, resolve_path

_VCS_DIRECTORIES_TO_EXCLUDE = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")
_MAX_COLUMNS = 500

_DEFAULT_HEAD_LIMIT = 250


def _apply_head_limit(
    items: list, limit: int | None, offset: int = 0
) -> tuple[list, int | None]:
    """Slice items by offset+limit.

    limit=0 → unlimited (offset only); limit=None → DEFAULT_HEAD_LIMIT. Returns
    (sliced, applied_limit) where applied_limit is set only when truncation
    actually occurred, so the model knows to paginate with offset.
    """
    if limit == 0:
        return items[offset:], None
    effective = limit if limit is not None else _DEFAULT_HEAD_LIMIT
    sliced = items[offset:offset + effective]
    truncated = len(items) - offset > effective
    return sliced, (effective if truncated else None)


def _format_limit_info(applied: int | None, offset: int) -> str:
    """formatLimitInfo: 'limit: N'[, 'offset: M']."""
    parts: list[str] = []
    if applied is not None:
        parts.append(f"limit: {applied}")
    if offset:
        parts.append(f"offset: {offset}")
    return ", ".join(parts)


def _render_grep_output(mode: str, lines: list[str], applied: int | None, offset: int) -> str:
    """mapToolResultToToolResultBlockParam — the exact
    model-facing text per output mode."""
    limit_info = _format_limit_info(applied, offset)
    if mode == "content":
        result = "\n".join(lines) if lines else "No matches found"
        if limit_info:
            result += f"\n\n[Showing results with pagination = {limit_info}]"
        return result
    if mode == "count":
        total = 0
        nfiles = 0
        for ln in lines:
            idx = ln.rfind(":")
            if idx > 0:
                try:
                    total += int(ln[idx + 1:])
                    nfiles += 1
                except ValueError:
                    pass
        raw = "\n".join(lines) if lines else "No matches found"
        occ = "occurrence" if total == 1 else "occurrences"
        fl = "file" if nfiles == 1 else "files"
        pag = f" with pagination = {limit_info}" if limit_info else ""
        return f"{raw}\n\nFound {total} total {occ} across {nfiles} {fl}.{pag}"
    if not lines:
        return "No files found"
    n = len(lines)
    fl = "file" if n == 1 else "files"
    extra = f" {limit_info}" if limit_info else ""
    return f"Found {n} {fl}{extra}\n" + "\n".join(lines)


def _relativize_line(line: str, mode: str, cwd) -> str:
    """Relativize the path portion of a ripgrep output line (toRelativePath,
    on LAST colon; files_with_matches → the whole line is the path."""
    if not line:
        return line
    if mode == "files_with_matches":
        return display_path_rel_to_cwd(line, cwd)
    idx = line.rfind(":") if mode == "count" else line.find(":")
    if idx > 0:
        return display_path_rel_to_cwd(line[:idx], cwd) + line[idx:]
    return line


def _sort_by_mtime(paths: list[str]) -> list[str]:
    """Sort file paths by mtime, newest first, filename as tiebreak (
    mid-scan doesn't break the batch."""
    def mtime(p: str) -> float:
        try:
            return os.stat(p).st_mtime
        except OSError:
            return 0.0
    return sorted(paths, key=lambda p: (-mtime(p), p))


class GrepParams(BaseModel):
    pattern: str = Field(..., description="The regular expression pattern to search for in file contents")
    path: str = Field(".", description="File or directory to search in (rg PATH). Defaults to current working directory.")
    glob: str | None = Field(
        None,
        description='Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob',
    )
    output_mode: str = Field(
        "files_with_matches",
        description='Output mode: "content", "files_with_matches" (default), or "count".',
    )
    context_before: int | None = Field(None, alias="-B", description="Lines before each match (rg -B). Requires output_mode=content.")
    context_after: int | None = Field(None, alias="-A", description="Lines after each match (rg -A). Requires output_mode=content.")
    context: int | None = Field(None, alias="-C", description="Lines before AND after each match (rg -C). Requires output_mode=content.")
    show_line_numbers: bool = Field(True, alias="-n", description="Show line numbers (rg -n). Requires output_mode=content. Default true.")
    case_insensitive: bool = Field(False, alias="-i", description="Case-insensitive search (rg -i).")
    type: str | None = Field(None, description="File type to search (rg --type): js, py, rust, go, java, etc.")
    head_limit: int | None = Field(None, description="Limit output to first N lines/entries (across all modes).")
    offset: int = Field(0, description="Skip first N lines/entries before head_limit. Default 0.")
    multiline: bool = Field(False, description="Multiline mode where . matches newlines (rg -U --multiline-dotall).")

    model_config = {"populate_by_name": True}


class GrepTool(Tool):
    name = "Grep"
    description = "A powerful search tool built on ripgrep"
    prompt = GREP_PROMPT
    kind = ToolKind.READ
    schema = GrepParams
    max_result_size_chars = 20_000

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    def get_permission_value(self, params: dict) -> str | None:
        return params.get("pattern")

    def get_path(self, params: dict) -> str | None:
        """Search root — used to gate reads to the working directory."""
        return params.get("path") or "."

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = GrepParams(**invocation.params)
        search_path = resolve_path(invocation.cwd, params.path)
        if not search_path.exists():
            return ToolResult.error_result(f"Path does not exist: {search_path}")

        rg = shutil.which("rg")
        if rg:
            return await self._run_ripgrep(rg, params, search_path, invocation.cwd)
        return self._run_python_fallback(params, search_path, invocation.cwd)


    def _build_args(self, params: GrepParams, search_path: Path) -> list[str]:
        args = ["--hidden"]
        for d in _VCS_DIRECTORIES_TO_EXCLUDE:
            args += ["--glob", f"!{d}"]
        args += ["--max-columns", str(_MAX_COLUMNS)]

        if params.multiline:
            args += ["-U", "--multiline-dotall"]
        if params.case_insensitive:
            args.append("-i")

        mode = params.output_mode
        if mode == "files_with_matches":
            args.append("-l")
        elif mode == "count":
            args.append("-c")

        if params.show_line_numbers and mode == "content":
            args.append("-n")

        if mode == "content":
            if params.context is not None:
                args += ["-C", str(params.context)]
            else:
                if params.context_before is not None:
                    args += ["-B", str(params.context_before)]
                if params.context_after is not None:
                    args += ["-A", str(params.context_after)]

        if params.pattern.startswith("-"):
            args += ["-e", params.pattern]
        else:
            args.append(params.pattern)

        if params.type:
            args += ["--type", params.type]

        if params.glob:
            for raw in params.glob.split():
                if "{" in raw and "}" in raw:
                    args += ["--glob", raw]
                else:
                    for g in raw.split(","):
                        if g:
                            args += ["--glob", g]

        args.append(str(search_path))
        return args

    async def _run_ripgrep(self, rg: str, params: GrepParams, search_path: Path, cwd) -> ToolResult:
        args = self._build_args(params, search_path)
        try:
            proc = await asyncio.create_subprocess_exec(
                rg, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return ToolResult.error_result("Grep timed out after 30s")
        except Exception as e:
            return ToolResult.error_result(f"ripgrep failed: {e}")

        if proc.returncode == 2:
            return ToolResult.error_result(
                f"ripgrep error: {stderr.decode('utf-8', 'replace').strip()[:200]}"
            )

        out = stdout.decode("utf-8", "replace")
        lines = out.splitlines()
        total = len(lines)

        if params.output_mode == "files_with_matches":
            lines = _sort_by_mtime(lines)

        lines, applied = _apply_head_limit(lines, params.head_limit, params.offset)

        lines = [_relativize_line(ln, params.output_mode, cwd) for ln in lines]

        body = _render_grep_output(params.output_mode, lines, applied, params.offset)

        return ToolResult.success_result(
            body,
            truncated=applied is not None,
            metadata={"mode": params.output_mode, "lines": total},
        )


    def _run_python_fallback(self, params: GrepParams, search_path: Path, cwd) -> ToolResult:
        try:
            flags = re.IGNORECASE if params.case_insensitive else 0
            if params.multiline:
                flags |= re.DOTALL
            pattern = re.compile(params.pattern, flags)
        except re.error as e:
            return ToolResult.error_result(f"Invalid regex pattern: {e}")

        files = self._find_files(search_path) if search_path.is_dir() else [search_path]
        mode = params.output_mode
        out_lines: list[str] = []
        total_matches = 0
        matched_files: list[str] = []

        for fp in files:
            try:
                content = fp.read_text(encoding="utf-8")
            except Exception:
                continue
            if params.glob and not _glob_match(fp, params.glob):
                continue

            if params.multiline:
                file_hits = len(pattern.findall(content))
            else:
                file_hits = sum(1 for ln in content.splitlines() if pattern.search(ln))

            if not file_hits:
                continue
            total_matches += file_hits

            if mode == "files_with_matches":
                matched_files.append(str(fp))
            elif mode == "count":
                rel = display_path_rel_to_cwd(str(fp), cwd)
                out_lines.append(f"{rel}:{file_hits}")
            else:
                rel = display_path_rel_to_cwd(str(fp), cwd)
                out_lines.append(f"=== {rel} ===")
                for i, ln in enumerate(content.splitlines(), start=1):
                    if pattern.search(ln):
                        out_lines.append(f"{i}:{ln}" if params.show_line_numbers else ln)

        if mode == "files_with_matches":
            out_lines = [
                display_path_rel_to_cwd(p, cwd) for p in _sort_by_mtime(matched_files)
            ]

        out_lines, applied = _apply_head_limit(out_lines, params.head_limit, params.offset)
        body = _render_grep_output(mode, out_lines, applied, params.offset)
        return ToolResult.success_result(
            body, truncated=applied is not None,
            metadata={"mode": mode, "matches": total_matches},
        )

    def _find_files(self, search_path: Path) -> list[Path]:
        files: list[Path] = []
        for root, dirs, filenames in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__", ".venv", "venv", *_VCS_DIRECTORIES_TO_EXCLUDE}]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                fp = Path(root) / filename
                if not is_binary(fp):
                    files.append(fp)
                    if len(files) >= 500:
                        return files
        return files


def _glob_match(path: Path, glob_spec: str) -> bool:
    """Match a file against a (possibly multi-pattern) glob spec, fallback only."""
    import fnmatch
    for raw in glob_spec.split():
        if "{" in raw and "}" in raw:
            pre, rest = raw.split("{", 1)
            inner, post = rest.split("}", 1)
            for opt in inner.split(","):
                if fnmatch.fnmatch(path.name, f"{pre}{opt}{post}"):
                    return True
        elif fnmatch.fnmatch(path.name, raw) or fnmatch.fnmatch(str(path), raw):
            return True
    return False
