"""
TaskGet tool — get details of a specific task.

Source: Claude Code src/tools/TaskGetTool/
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.shared.taskRegistry import all_tasks
from tools.builtin.TaskGetTool.prompt import DESCRIPTION, TASK_GET_TOOL_PROMPT


class TaskGetParams(BaseModel):
    task_id: str = Field(..., description="ID of the task to get")


class TaskGetTool(Tool):
    name = "TaskGet"
    description = DESCRIPTION
    prompt = TASK_GET_TOOL_PROMPT
    kind = ToolKind.READ
    schema = TaskGetParams

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskGetParams(**invocation.params)
        task = all_tasks().get(params.task_id)
        if not task:
            return ToolResult.error_result(f"Task not found: {params.task_id}")

        return ToolResult.success_result(
            f"Task #{task.id}\n"
            f"  Subject: {task.subject}\n"
            f"  Status: {task.status}\n"
            f"  Description: {task.description or '(none)'}"
        )
