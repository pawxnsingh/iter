"""Mirrors Claude Code src/tools/FileEditTool/constants.ts."""

FILE_EDIT_TOOL_NAME = "Edit"

# Permission pattern for granting session-level access to the project's .ai-agent/ folder
PROJECT_FOLDER_PERMISSION_PATTERN = "/.ai-agent/**"

# Permission pattern for granting session-level access to the global ~/.ai-agent/ folder
GLOBAL_FOLDER_PERMISSION_PATTERN = "~/.ai-agent/**"

FILE_UNEXPECTEDLY_MODIFIED_ERROR = (
    "File has been unexpectedly modified. Read it again before attempting to write it."
)
