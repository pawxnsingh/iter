"""
Grep tool — ripgrep wrapper, Claude Code parity.

Source: claude-code/src/tools/GrepTool/GrepTool.ts. Shells out to `rg` and
builds args exactly as CC does: output modes (content / files_with_matches /
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
from utils.paths import is_binary, resolve_path

# VCS dirs CC excludes from every search (rg --glob '!dir').
_VCS_DIRECTORIES_TO_EXCLUDE = (".git", ".hg", ".svn")
_MAX_COLUMNS = 500


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
    # CC parity: GrepTool.ts maxResultSizeChars: 20_000
    max_result_size_chars = 20_000

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    def get_permission_value(self, params: dict) -> str | None:
        return params.get("pattern")

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = GrepParams(**invocation.params)
        search_path = resolve_path(invocation.cwd, params.path)
        if not search_path.exists():
            return ToolResult.error_result(f"Path does not exist: {search_path}")

        rg = shutil.which("rg")
        if rg:
            return await self._run_ripgrep(rg, params, search_path)
        return self._run_python_fallback(params, search_path)

    # ── ripgrep path (CC parity) ──────────────────────────────────────

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

        # Pattern starting with '-' must be passed via -e so rg doesn't read it
        # as a flag.
        if params.pattern.startswith("-"):
            args += ["-e", params.pattern]
        else:
            args.append(params.pattern)

        if params.type:
            args += ["--type", params.type]

        if params.glob:
            for raw in params.glob.split():
                args += ["--glob", raw]

        args.append(str(search_path))
        return args

    async def _run_ripgrep(self, rg: str, params: GrepParams, search_path: Path) -> ToolResult:
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

        # rg exit codes: 0 = matches, 1 = no matches, 2 = error.
        if proc.returncode == 2:
            return ToolResult.error_result(
                f"ripgrep error: {stderr.decode('utf-8', 'replace').strip()[:200]}"
            )

        out = stdout.decode("utf-8", "replace")
        lines = out.splitlines()

        if not lines:
            return ToolResult.success_result(
                f"No matches found for pattern '{params.pattern}'",
                metadata={"matches": 0, "mode": params.output_mode},
            )

        # head_limit + offset post-processing (CC: "| tail -n +offset | head -N")
        total = len(lines)
        if params.offset:
            lines = lines[params.offset:]
        truncated = False
        if params.head_limit is not None and len(lines) > params.head_limit:
            lines = lines[: params.head_limit]
            truncated = True

        body = "\n".join(lines)
        if truncated:
            body += f"\n... [results truncated to {params.head_limit}]"

        return ToolResult.success_result(
            body,
            truncated=truncated,
            metadata={"mode": params.output_mode, "lines": total},
        )

    # ── Python fallback (no rg) ───────────────────────────────────────

    def _run_python_fallback(self, params: GrepParams, search_path: Path) -> ToolResult:
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
            rel = str(fp)

            if mode == "files_with_matches":
                matched_files.append(rel)
            elif mode == "count":
                out_lines.append(f"{rel}:{file_hits}")
            else:  # content
                out_lines.append(f"=== {rel} ===")
                for i, ln in enumerate(content.splitlines(), start=1):
                    if pattern.search(ln):
                        out_lines.append(f"{i}:{ln}" if params.show_line_numbers else ln)

        if mode == "files_with_matches":
            out_lines = matched_files

        if not out_lines:
            return ToolResult.success_result(
                f"No matches found for pattern '{params.pattern}'",
                metadata={"matches": 0, "mode": mode},
            )

        if params.offset:
            out_lines = out_lines[params.offset:]
        truncated = False
        if params.head_limit is not None and len(out_lines) > params.head_limit:
            out_lines = out_lines[: params.head_limit]
            truncated = True

        body = "\n".join(out_lines)
        if truncated:
            body += f"\n... [results truncated to {params.head_limit}]"
        return ToolResult.success_result(
            body, truncated=truncated,
            metadata={"mode": mode, "matches": total_matches},
        )

    def _find_files(self, search_path: Path) -> list[Path]:
        files: list[Path] = []
        for root, dirs, filenames in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__", ".git", ".venv", "venv", ".hg", ".svn"}]
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
        # crude brace expansion: *.{ts,tsx} → *.ts, *.tsx
        if "{" in raw and "}" in raw:
            pre, rest = raw.split("{", 1)
            inner, post = rest.split("}", 1)
            for opt in inner.split(","):
                if fnmatch.fnmatch(path.name, f"{pre}{opt}{post}"):
                    return True
        elif fnmatch.fnmatch(path.name, raw) or fnmatch.fnmatch(str(path), raw):
            return True
    return False
