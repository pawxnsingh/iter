from pydantic import BaseModel, Field

from tools.base import (
    FileDiff,
    Tool,
    ToolConfirmation,
    ToolInvocation,
    ToolKind,
    ToolResult,
)
from utils.paths import ensure_parent_directory, resolve_path


class WriteFileParams(BaseModel):
    path: str = Field(
        ...,
        description="The file system path to the file to be write. Can be an absolute path or relative to the current working directory.",
    )

    content: str = Field(..., description="Content to write to the file")
    create_directories: bool = Field(
        True, description="Create parent directories if they don't exist"
    )


class WriteFileTool(Tool):
    name = "Write"
    description = (
        "Writes a file to the local filesystem. This tool will overwrite the existing file if "
        "there is one at the provided path. If this is an existing file, you MUST use the Read "
        "tool first to read the file's contents. Prefer the Edit tool for modifying existing "
        "files — it only sends the diff. Only use this tool to create new files or for complete rewrites. "
        "NEVER create documentation files (*.md) or README files unless explicitly requested."
    )

    schema = WriteFileParams
    kind = ToolKind.WRITE

    def get_path(self, params: dict) -> str | None:
        return params.get("path")

    async def get_confirmation(
        self, invocation: ToolInvocation
    ) -> ToolConfirmation | None:
        params = WriteFileParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.path)

        is_new_file = not path.exists()

        old_content = ""
        if not is_new_file:
            try:
                old_content = path.read_text(encoding="utf-8")
            except Exception:
                pass

        diff = FileDiff(
            path=path,
            old_content=old_content,
            new_content=params.content,
            is_new_file=is_new_file,
        )

        action = "Created" if is_new_file else "Updated"

        return ToolConfirmation(
            tool_name=self.name,
            params=invocation.params,
            description=f"{action} file: {path}",
            diff=diff,
            affected_paths=[path],
            is_dangerous=not is_new_file,
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = WriteFileParams(**invocation.params)
        path = resolve_path(invocation.cwd, params.path)

        # first check if that new file
        is_new_file = not path.exists()
        old_content = ""

        if not is_new_file:
            old_content = path.read_text(encoding="utf-8")

        try:
            if params.create_directories:
                ensure_parent_directory(path)
            elif not path.parent.exists():
                return ToolResult.error_result(
                    f"Parent directory does not exist: {path.parent}"
                )

            path.write_text(params.content, encoding="utf-8")

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
