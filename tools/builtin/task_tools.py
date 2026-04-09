"""
Task management tools — full CRUD matching Claude Code.

Source: Claude Code src/tools/TaskCreateTool/, TaskGetTool/, TaskListTool/,
        TaskUpdateTool/, TaskStopTool/, TaskOutputTool/

Tasks are background work units that can be tracked, updated, and stopped.
Used by the Agent tool for background execution tracking.
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


@dataclass
class Task:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"  # pending, in_progress, completed, deleted
    owner: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# Global task store (shared across tool instances)
_tasks: dict[str, Task] = {}


class TaskCreateParams(BaseModel):
    subject: str = Field(..., description="Brief title for the task")
    description: str = Field(default="", description="What needs to be done")


class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = (
        "Create a new task to track progress on multi-step work. "
        "Use for complex tasks that benefit from structured tracking."
    )
    kind = ToolKind.MEMORY
    schema = TaskCreateParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskCreateParams(**invocation.params)
        task_id = str(uuid_mod.uuid4())[:8]
        task = Task(id=task_id, subject=params.subject, description=params.description)
        _tasks[task_id] = task
        return ToolResult.success_result(
            f"Task #{task_id} created: {params.subject}",
            metadata={"task_id": task_id},
        )


class TaskUpdateParams(BaseModel):
    task_id: str = Field(..., description="ID of the task to update")
    status: str | None = Field(None, description="New status: pending/in_progress/completed/deleted")
    subject: str | None = Field(None, description="New subject")
    description: str | None = Field(None, description="New description")


class TaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = "Update a task's status, subject, or description."
    kind = ToolKind.MEMORY
    schema = TaskUpdateParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskUpdateParams(**invocation.params)
        task = _tasks.get(params.task_id)
        if not task:
            return ToolResult.error_result(f"Task not found: {params.task_id}")

        if params.status:
            task.status = params.status
        if params.subject:
            task.subject = params.subject
        if params.description:
            task.description = params.description

        return ToolResult.success_result(f"Task #{task.id} updated: [{task.status}] {task.subject}")


class TaskListParams(BaseModel):
    pass


class TaskListTool(Tool):
    name = "TaskList"
    description = "List all tasks and their statuses."
    kind = ToolKind.READ
    schema = TaskListParams

    def is_read_only(self, params: dict) -> bool:
        return True

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        if not _tasks:
            return ToolResult.success_result("No tasks.")

        icons = {"pending": "○", "in_progress": "◐", "completed": "●", "deleted": "✗"}
        lines = []
        for task in _tasks.values():
            if task.status == "deleted":
                continue
            icon = icons.get(task.status, "?")
            lines.append(f"  {icon} #{task.id} [{task.status}] {task.subject}")

        return ToolResult.success_result("\n".join(lines) if lines else "No active tasks.")


class TaskGetParams(BaseModel):
    task_id: str = Field(..., description="ID of the task to get")


class TaskGetTool(Tool):
    name = "TaskGet"
    description = "Get details of a specific task."
    kind = ToolKind.READ
    schema = TaskGetParams

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskGetParams(**invocation.params)
        task = _tasks.get(params.task_id)
        if not task:
            return ToolResult.error_result(f"Task not found: {params.task_id}")

        return ToolResult.success_result(
            f"Task #{task.id}\n"
            f"  Subject: {task.subject}\n"
            f"  Status: {task.status}\n"
            f"  Description: {task.description or '(none)'}"
        )


class TaskStopParams(BaseModel):
    task_id: str = Field(..., description="ID of the task to stop")


class TaskStopTool(Tool):
    name = "TaskStop"
    description = "Stop/cancel a running task."
    kind = ToolKind.MEMORY
    schema = TaskStopParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TaskStopParams(**invocation.params)
        task = _tasks.get(params.task_id)
        if not task:
            return ToolResult.error_result(f"Task not found: {params.task_id}")

        task.status = "deleted"
        return ToolResult.success_result(f"Task #{task.id} stopped.")
