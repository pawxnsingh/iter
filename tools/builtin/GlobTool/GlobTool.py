import asyncio
import shutil

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.GlobTool.prompt import GLOB_PROMPT
from utils.paths import display_path_rel_to_cwd, resolve_path

_GLOB_MAX_RESULTS = 100


class GlobParams(BaseModel):
    pattern: str = Field(..., description="Glob pattern to match")
    path: str = Field(
        ".", description="Directory to search in (default: current directory)"
    )


class GlobTool(Tool):
    name = "Glob"
    description = "Fast file pattern matching tool that works with any codebase size."
    prompt = GLOB_PROMPT
    kind = ToolKind.READ
    schema = GlobParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    def get_permission_value(self, params: dict) -> str | None:
        """Pattern matched against `Glob(<pattern>)` rules."""
        return params.get("pattern")

    def get_path(self, params: dict) -> str | None:
        """Search root — used to gate reads to the working directory."""
        return params.get("path") or "."

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = GlobParams(**invocation.params)

        search_path = resolve_path(invocation.cwd, params.path)

        if not search_path.exists() or not search_path.is_dir():
            return ToolResult.error_result(f"Directory does not exist: {search_path}")

        try:
            matches = await self._glob_matches(params.pattern, search_path)
        except Exception as e:
            return ToolResult.error_result(f"Error searching: {e}")

        output_lines = []

        for file_path in matches[:_GLOB_MAX_RESULTS]:
            output_lines.append(
                display_path_rel_to_cwd(str(file_path), invocation.cwd)
            )

        if len(matches) > _GLOB_MAX_RESULTS:
            output_lines.append(
                "(Results are truncated. Consider using a more specific path or pattern.)"
            )

        return ToolResult.success_result(
            "\n".join(output_lines),
            metadata={
                "path": str(search_path),
                "matches": len(matches),
            },
        )

    async def _glob_matches(self, pattern: str, search_path) -> list:
        """Return matching file paths, RECURSIVELY, sorted by mtime oldest-first.

        Runs `rg --files --glob <pattern> --sort=modified --no-ignore --hidden`,
        so a bare pattern like `*.py` matches at ANY depth (ripgrep applies the
        glob across the whole subtree). Python's `Path.glob('*.py')` is
        NON-recursive, so we shell out to ripgrep and fall back to recursive
        pathlib only when rg is unavailable. GLOB_NO_IGNORE / GLOB_HIDDEN env
        vars (default true) gate the --no-ignore / --hidden flags.
        """
        rg = shutil.which("rg")
        if rg:
            import os
            def _truthy(v, default="true"):
                return (v or default).lower().strip() in ("1", "true", "yes", "on")
            no_ignore = _truthy(os.environ.get("GLOB_NO_IGNORE"))
            hidden = _truthy(os.environ.get("GLOB_HIDDEN"))
            args = ["--files", "--glob", pattern, "--sort=modified"]
            if no_ignore:
                args.append("--no-ignore")
            if hidden:
                args.append("--hidden")
            proc = await asyncio.create_subprocess_exec(
                rg, *args,
                cwd=str(search_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            rel = stdout.decode("utf-8", "replace").splitlines()
            return [search_path / p for p in rel if p]

        if "/" in pattern:
            it = search_path.glob(pattern)
        else:
            it = search_path.rglob(pattern)
        matches = [p for p in it if p.is_file()]

        def _mtime(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        matches.sort(key=_mtime)
        return matches
