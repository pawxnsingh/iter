"""
TaskStop tool — stop/cancel a running task.

Source: src/tools/TaskStopTool/
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.shared.taskRegistry import all_tasks
from tools.builtin.TaskStopTool.prompt import DESCRIPTION


class TaskStopParams(BaseModel):
    task_id: str = Field(..., description="ID of the task to stop")


class TaskStopTool(Tool):
    name = "TaskStop"
    description = DESCRIPTION
    kind = ToolKind.MEMORY
    schema = TaskStopParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskStopParams(**invocation.params)
        task = all_tasks().get(params.task_id)
        if not task:
            return ToolResult.error_result(f"Task not found: {params.task_id}")

        task.status = "deleted"
        return ToolResult.success_result(f"Task #{task.id} stopped.")
