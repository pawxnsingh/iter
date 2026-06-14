"""
Shared task registry used by TaskCreate / TaskGet / TaskList / TaskUpdate / TaskStop.

Mirrors the tools/shared/ pattern — content shared across multiple
tool directories lives here rather than being duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"
    owner: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


_tasks: dict[str, Task] = {}


def all_tasks() -> dict[str, Task]:
    return _tasks


def _task_hook_executor():
    from hooks.config import get_hooks_from_snapshot
    from hooks.executor import HookExecutor

    snapshot = get_hooks_from_snapshot()
    if not snapshot:
        return None
    return HookExecutor(snapshot)


async def _fire_task_hook(event_value: str, task: Task) -> str | None:
    import logging

    executor = _task_hook_executor()
    if executor is None:
        return None
    try:
        result = await executor.run_event_hooks(
            event=event_value,
            data={"task_id": task.id, "task_subject": task.subject,
                  "task_description": task.description},
        )
        return result.blocking_error
    except Exception:
        logging.getLogger(__name__).debug("%s hook failed", event_value, exc_info=True)
        return None


async def fire_task_created(task: Task) -> str | None:
    """Fire TaskCreated; return a blocking error string if a hook blocks, else None."""
    from core_types.hooks import HookEvent
    return await _fire_task_hook(HookEvent.TASK_CREATED.value, task)


async def fire_task_completed(task: Task) -> str | None:
    """Fire TaskCompleted; return a blocking error string if a hook blocks, else None."""
    from core_types.hooks import HookEvent
    return await _fire_task_hook(HookEvent.TASK_COMPLETED.value, task)
