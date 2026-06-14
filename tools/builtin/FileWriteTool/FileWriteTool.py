from pydantic import BaseModel, Field

from tools.base import (
    FileDiff,
    Tool,
    ToolInvocation,
    ToolKind,
    ToolResult,
)
from tools.builtin.FileWriteTool.prompt import WRITE_PROMPT
from utils.paths import ensure_parent_directory, resolve_path


class WriteFileParams(BaseModel):
    file_path: str = Field(
        ...,
        description="The absolute path to the file to write (must be absolute, not relative).",
    )

    content: str = Field(..., description="Content to write to the file")


class FileWriteTool(Tool):
    name = "Write"
    description = "Writes a file to the local filesystem."
    prompt = WRITE_PROMPT

    schema = WriteFileParams
    kind = ToolKind.WRITE

    def get_path(self, params: dict) -> str | None:
        return params.get("file_path")

    def get_permission_value(self, params: dict) -> str | None:
        """Path matched against `Write(<pattern>)` rules."""
        return params.get("file_path")

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = WriteFileParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.file_path)

        is_new_file = not path.exists()
        old_content = ""

        if not is_new_file:
            old_content = path.read_text(encoding="utf-8")
            from tools.builtin.shared.readFileState import check_freshness
            freshness_error = check_freshness(path, old_content)
            if freshness_error:
                return ToolResult.error_result(freshness_error)

        try:
            ensure_parent_directory(path)

            normalized = params.content.replace("\r\n", "\n").replace("\r", "\n")
            path.write_text(normalized, encoding="utf-8", newline="")
            from tools.builtin.shared.readFileState import record_read
            record_read(path, normalized)

            action = "Created" if is_new_file else "Updated"
            line_count = len(params.content.splitlines())

            return ToolResult.success_result(
                output=f"{action} {path} {line_count} lines",
                diff=FileDiff(
                    path=path,
                    old_content=old_content,
                    new_content=params.content,
                    is_new_file=is_new_file,
                ),
                metadata={
                    "path": str(path),
                    "is_new_file": is_new_file,
                    "lines": line_count,
                    "bytes": len(params.content.encode("utf-8")),
                },
            )

        except OSError as e:
            return ToolResult.error_result(f"Failed to write file: {e}")
