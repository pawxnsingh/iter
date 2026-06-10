"""
Shared registry for background subagent results.

Mirrors Claude Code's tools/shared/ pattern: state shared across tool
directories lives here, not duplicated. AgentTool (producer, _run_background)
writes results keyed by task_id; AgentResultTool (consumer) reads them so the
model can collect what a backgrounded subagent produced.

Without this, background results were written to a per-instance dict that no
other tool could ever read.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class BackgroundAgent:
    task_id: str
    agent_type: str
    task: asyncio.Task
    result: str | None = None  # populated when the task completes


# Global store, shared across tool instances.
_agents: dict[str, BackgroundAgent] = {}


def register_background_agent(task_id: str, agent_type: str, task: asyncio.Task) -> None:
    _agents[task_id] = BackgroundAgent(task_id=task_id, agent_type=agent_type, task=task)


def set_background_result(task_id: str, result: str) -> None:
    agent = _agents.get(task_id)
    if agent is not None:
        agent.result = result


def get_background_agent(task_id: str) -> BackgroundAgent | None:
    return _agents.get(task_id)


def all_background_agents() -> dict[str, BackgroundAgent]:
    return _agents
