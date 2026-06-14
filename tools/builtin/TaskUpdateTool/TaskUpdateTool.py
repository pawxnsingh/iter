"""
TaskUpdate tool — update an existing task's fields.

Source: src/tools/TaskUpdateTool/
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.shared.taskRegistry import all_tasks, fire_task_completed
from tools.builtin.TaskUpdateTool.prompt import (
    DESCRIPTION,
    TASK_UPDATE_TOOL_PROMPT,
)


class TaskUpdateParams(BaseModel):
    task_id: str = Field(..., description="ID of the task to update")
    status: str | None = Field(None, description="New status: pending/in_progress/completed/deleted")
    subject: str | None = Field(None, description="New subject")
    description: str | None = Field(None, description="New description")


class TaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = DESCRIPTION
    prompt = TASK_UPDATE_TOOL_PROMPT
    kind = ToolKind.MEMORY
    schema = TaskUpdateParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskUpdateParams(**invocation.params)
        task = all_tasks().get(params.task_id)
        if not task:
            return ToolResult.error_result(f"Task not found: {params.task_id}")

        prev_status = task.status
        newly_completed = params.status == "completed" and prev_status != "completed"

        if params.status:
            task.status = params.status
        if params.subject:
            task.subject = params.subject
        if params.description:
            task.description = params.description

        if newly_completed:
            blocking = await fire_task_completed(task)
            if blocking:
                task.status = prev_status
                return ToolResult.error_result(
                    f"Task completion blocked by hook: {blocking}"
                )

        return ToolResult.success_result(f"Task #{task.id} updated: [{task.status}] {task.subject}")
