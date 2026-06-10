from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.GlobTool.prompt import GLOB_PROMPT
from utils.paths import display_path_rel_to_cwd, resolve_path


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

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = GlobParams(**invocation.params)

        search_path = resolve_path(invocation.cwd, params.path)

        if not search_path.exists() or not search_path.is_dir():
            return ToolResult.error_result(f"Directory does not exist: {search_path}")

        try:
            matches = list(search_path.glob(params.pattern))
            matches = [p for p in matches if p.is_file()]
        except Exception as e:
            return ToolResult.error_result(f"Error searching: {e}")

        # Sort by modification time, oldest first — CC parity (utils/glob.ts
        # uses rg --sort=modified). Agents rely on the most recently changed
        # files appearing last (closest to the model's attention).
        def _mtime(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        matches.sort(key=_mtime)

        output_lines = []

        for file_path in matches[:1000]:
            output_lines.append(
                display_path_rel_to_cwd(str(file_path), invocation.cwd)
            )

        if len(matches) > 1000:
            output_lines.append("...(limited to 1000 results)")

        return ToolResult.success_result(
            "\n".join(output_lines),
            metadata={
                "path": str(search_path),
                "matches": len(matches),
            },
        )

