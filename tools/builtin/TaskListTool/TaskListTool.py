"""
TaskList tool — list active tasks.

Source: Claude Code src/tools/TaskListTool/
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.shared.taskRegistry import all_tasks
from tools.builtin.TaskListTool.prompt import DESCRIPTION, TASK_LIST_TOOL_PROMPT


class TaskListParams(BaseModel):
    pass


class TaskListTool(Tool):
    name = "TaskList"
    description = DESCRIPTION
    prompt = TASK_LIST_TOOL_PROMPT
    kind = ToolKind.READ
    schema = TaskListParams

    def is_read_only(self, params: dict) -> bool:
        return True

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        tasks = all_tasks()
        if not tasks:
            return ToolResult.success_result("No tasks.")

        icons = {"pending": "○", "in_progress": "◐", "completed": "●", "deleted": "✗"}
        lines = []
        for task in tasks.values():
            if task.status == "deleted":
                continue
            icon = icons.get(task.status, "?")
            lines.append(f"  {icon} #{task.id} [{task.status}] {task.subject}")

        return ToolResult.success_result("\n".join(lines) if lines else "No active tasks.")
