"""
TaskCreate tool — create a new tracked task.

Source: Claude Code src/tools/TaskCreateTool/
"""

from __future__ import annotations

import uuid as uuid_mod

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.shared.taskRegistry import Task, all_tasks, fire_task_created
from tools.builtin.TaskCreateTool.prompt import (
    DESCRIPTION,
    TASK_CREATE_TOOL_PROMPT,
)


class TaskCreateParams(BaseModel):
    subject: str = Field(..., description="Brief title for the task")
    description: str = Field(default="", description="What needs to be done")


class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = DESCRIPTION
    prompt = TASK_CREATE_TOOL_PROMPT
    kind = ToolKind.MEMORY
    schema = TaskCreateParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskCreateParams(**invocation.params)
        task_id = str(uuid_mod.uuid4())[:8]
        task = Task(id=task_id, subject=params.subject, description=params.description)
        all_tasks()[task_id] = task

        # TaskCreated hook (CC: a blocking hook rolls back the create).
        blocking = await fire_task_created(task)
        if blocking:
            del all_tasks()[task_id]
            return ToolResult.error_result(f"Task creation blocked by hook: {blocking}")

        return ToolResult.success_result(
            f"Task #{task_id} created: {params.subject}",
            metadata={"task_id": task_id},
        )
