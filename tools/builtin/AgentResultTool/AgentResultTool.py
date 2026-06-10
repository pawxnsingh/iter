"""
AgentResult tool — collect the output of a background subagent.

Source: companion to AgentTool's run_in_background path. The Agent tool returns
a task_id when launched in background; this tool reads the shared
agentRegistry to return that agent's status/output.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.AgentResultTool.prompt import (
    AGENT_RESULT_TOOL_PROMPT,
    DESCRIPTION,
)
from tools.builtin.shared.agentRegistry import all_background_agents, get_background_agent


class AgentResultParams(BaseModel):
    task_id: str | None = Field(
        default=None,
        description="The background task_id returned by the Agent tool. Omit to list all.",
    )


class AgentResultTool(Tool):
    name = "AgentResult"
    description = DESCRIPTION
    prompt = AGENT_RESULT_TOOL_PROMPT
    kind = ToolKind.READ
    schema = AgentResultParams

    def is_read_only(self, params: dict) -> bool:
        return True

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = AgentResultParams(**invocation.params)

        if not params.task_id:
            agents = all_background_agents()
            if not agents:
                return ToolResult.success_result("No background agents have been launched.")
            lines = ["Background agents:"]
            for tid, a in agents.items():
                status = "done" if a.result is not None else (
                    "running" if not a.task.done() else "finishing"
                )
                lines.append(f"  #{tid} [{a.agent_type}] — {status}")
            return ToolResult.success_result("\n".join(lines))

        agent = get_background_agent(params.task_id)
        if agent is None:
            return ToolResult.error_result(
                f"No background agent with task_id '{params.task_id}'."
            )
        if agent.result is not None:
            return ToolResult.success_result(
                agent.result,
                metadata={"task_id": params.task_id, "status": "done"},
            )
        if not agent.task.done():
            return ToolResult.success_result(
                f"Agent #{params.task_id} ({agent.agent_type}) is still running. "
                f"Try again shortly.",
                metadata={"task_id": params.task_id, "status": "running"},
            )
        # Task is done but result not yet recorded — race; report finishing.
        return ToolResult.success_result(
            f"Agent #{params.task_id} is finishing; result not yet available.",
            metadata={"task_id": params.task_id, "status": "finishing"},
        )
