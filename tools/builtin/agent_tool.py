"""
Agent tool — launch specialized subagents.

Source: Claude Code src/tools/AgentTool/

Supports:
  - Built-in agent types (Explore, Plan, general-purpose)
  - Custom agent types from settings.json
  - Background execution (run_in_background)
  - Tool restriction per agent type
  - Model override per agent
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from config.config import Config
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class AgentDefinition:
    """
    Agent type definition.
    Claude Code: AgentDefinition in loadAgentsDir.ts
    """
    agent_type: str
    description: str
    when_to_use: str
    tools: list[str] | None = None  # None or ["*"] = all tools
    max_turns: int = 200
    model: str = "inherit"
    system_prompt: str | None = None


# Built-in agents matching Claude Code
BUILT_IN_AGENTS: dict[str, AgentDefinition] = {
    "general-purpose": AgentDefinition(
        agent_type="general-purpose",
        description="General-purpose agent for complex, multi-step tasks",
        when_to_use="When the task requires multiple tools and steps",
        tools=None,  # All tools
        max_turns=200,
    ),
    "Explore": AgentDefinition(
        agent_type="Explore",
        description="Fast codebase exploration specialist. READ-ONLY.",
        when_to_use="Quick file finding, codebase search, answering questions about code",
        tools=["Read", "Grep", "Glob", "ListDir", "WebFetch", "WebSearch"],
        max_turns=30,
        system_prompt="You are a codebase exploration specialist. Use read-only tools to find and analyze code. Do NOT modify any files.",
    ),
    "Plan": AgentDefinition(
        agent_type="Plan",
        description="Software architect for designing implementation plans. READ-ONLY.",
        when_to_use="Design implementation strategy, architecture analysis",
        tools=["Read", "Grep", "Glob", "ListDir", "WebFetch", "WebSearch"],
        max_turns=30,
        system_prompt="You are a software architect. Explore the codebase and design an implementation approach. Do NOT modify files.",
    ),
}


class AgentToolParams(BaseModel):
    prompt: str = Field(..., description="The task for the agent to perform")
    subagent_type: str = Field(
        default="general-purpose",
        description="Type of agent to spawn (general-purpose, Explore, Plan, or custom)",
    )
    description: str = Field(
        default="",
        description="Short description (3-5 words) of what the agent will do",
    )
    run_in_background: bool = Field(
        default=False,
        description="Run in background (returns immediately, notifies on completion)",
    )
    model: str | None = Field(
        default=None,
        description="Optional model override for this agent",
    )


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Launch a specialized agent to handle complex, multi-step tasks autonomously. "
        "Each agent type has specific capabilities and tools. Use subagent_type to select: "
        "'Explore' for read-only codebase search, 'Plan' for architecture design, "
        "'general-purpose' for full capability tasks."
    )
    kind = ToolKind.WRITE  # Can modify files via sub-agent
    schema = AgentToolParams

    def __init__(self, config: Config):
        super().__init__(config)
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._background_results: dict[str, str] = {}

    def is_concurrency_safe(self, params: dict) -> bool:
        return False  # Agents are not concurrency-safe

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = AgentToolParams(**invocation.params)

        # Find agent definition
        definition = BUILT_IN_AGENTS.get(params.subagent_type)
        if not definition:
            available = ", ".join(sorted(BUILT_IN_AGENTS.keys()))
            return ToolResult.error_result(
                f"Unknown agent type: {params.subagent_type}\nAvailable: {available}"
            )

        if params.run_in_background:
            return await self._run_background(params, definition, invocation)
        else:
            return await self._run_foreground(params, definition, invocation)

    async def _run_foreground(
        self, params: AgentToolParams, definition: AgentDefinition, invocation: ToolInvocation
    ) -> ToolResult:
        """Run agent synchronously and return result."""
        from agent.agent import Agent

        # Build sub-config with restricted tools
        config_dict = self.config.to_dict()
        config_dict["max_turns"] = definition.max_turns
        if definition.tools:
            config_dict["allowed_tools"] = definition.tools
        if params.model and params.model != "inherit":
            config_dict["model"] = {"model": params.model}

        sub_config = Config(**config_dict)

        result_parts = []
        tool_calls = []

        try:
            async with Agent(sub_config) as agent:
                prompt = params.prompt
                if definition.system_prompt:
                    prompt = f"{definition.system_prompt}\n\nTASK: {prompt}"

                async for event in agent.run(prompt):
                    from agent.event import AgentEventType
                    if event.type == AgentEventType.TEXT_COMPLETE:
                        text = event.data.get("content", "")
                        if text:
                            result_parts.append(text)
                    elif event.type == AgentEventType.TOOL_CALL_START:
                        tool_calls.append(event.data.get("name", ""))
                    elif event.type == AgentEventType.AGENT_ERROR:
                        error = event.data.get("error", "")
                        if error:
                            result_parts.append(f"Error: {error}")

        except Exception as e:
            return ToolResult.error_result(f"Agent failed: {e}")

        result_text = "\n".join(result_parts) or "Agent completed with no output."
        tools_used = ", ".join(tool_calls) if tool_calls else "None"

        return ToolResult.success_result(
            f"Agent '{definition.agent_type}' completed.\n"
            f"Tools used: {tools_used}\n\n"
            f"Result:\n{result_text}",
            metadata={"agent_type": definition.agent_type, "tools_called": tool_calls},
        )

    async def _run_background(
        self, params: AgentToolParams, definition: AgentDefinition, invocation: ToolInvocation
    ) -> ToolResult:
        """Launch agent in background and return immediately."""
        import uuid
        task_id = str(uuid.uuid4())[:8]

        async def _run():
            result = await self._run_foreground(params, definition, invocation)
            self._background_results[task_id] = result.output

        task = asyncio.create_task(_run())
        self._background_tasks[task_id] = task

        return ToolResult.success_result(
            f"Agent '{definition.agent_type}' launched in background (task: {task_id}).\n"
            f"You will be notified when it completes.",
            metadata={"task_id": task_id, "background": True},
        )
