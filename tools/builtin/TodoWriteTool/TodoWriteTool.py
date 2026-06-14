"""
TodoWrite tool — structured task management matching.

Source: src/tools/TodoWriteTool/

Task states: pending → in_progress → completed
The model submits the FULL todo list each time (replace, not patch).
This ensures consistency and lets the model reorder/restructure freely.
"""

from __future__ import annotations


from pydantic import BaseModel, Field

from config.config import Config
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.TodoWriteTool.prompt import TODO_PROMPT


class TodoItem(BaseModel):
    content: str = Field(..., description="What needs to be done (imperative form)")
    status: str = Field(
        "pending",
        description="Task status: 'pending', 'in_progress', or 'completed'",
    )


class TodoWriteParams(BaseModel):
    todos: list[TodoItem] = Field(
        ...,
        description="The complete updated todo list. Submit the FULL list each time.",
    )


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = "Update the todo list for the current session. To be used proactively and often to track progress and pending tasks. Make sure that at least one task is in_progress at all times. Always provide both content (imperative) and activeForm (present continuous) for each task."
    prompt = TODO_PROMPT
    kind = ToolKind.MEMORY
    schema = TodoWriteParams

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._todos: list[dict[str, str]] = []

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TodoWriteParams(**invocation.params)

        self._todos = [
            {"content": t.content, "status": t.status}
            for t in params.todos
        ]

        pending = sum(1 for t in self._todos if t["status"] == "pending")
        in_progress = sum(1 for t in self._todos if t["status"] == "in_progress")
        completed = sum(1 for t in self._todos if t["status"] == "completed")

        lines = [f"Tasks updated: {pending} pending, {in_progress} in progress, {completed} completed", ""]

        for t in self._todos:
            icon = {"pending": "○", "in_progress": "◐", "completed": "●"}.get(t["status"], "?")
            lines.append(f"  {icon} [{t['status']}] {t['content']}")

        return ToolResult.success_result(
            "\n".join(lines),
            metadata={
                "total": len(self._todos),
                "pending": pending,
                "in_progress": in_progress,
                "completed": completed,
            },
        )

    def get_todos(self) -> list[dict[str, str]]:
        """Get current todo list (for /status command)."""
        return list(self._todos)
