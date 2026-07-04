"""
Agent tool — launch specialized subagents.

Source: src/tools/AgentTool/

Supports:
  - Built-in agent types (Explore, Plan, general-purpose)
  - Custom agent types from settings.json
  - Background execution (run_in_background)
  - Tool restriction per agent type
  - Model override per agent
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from config.config import Config
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.AgentTool.built_in import BUILT_IN_AGENTS
from tools.builtin.AgentTool.built_in_types import AgentDefinition
from tools.builtin.AgentTool.prompt import AGENT_PROMPT
from utils.logger import get_logger

_ALL_AGENT_DISALLOWED_TOOLS = frozenset(
    {"EnterPlanMode", "ExitPlanMode", "Agent", "AskUserQuestion", "TaskStop"}
)

logger = get_logger(__name__)


class AgentToolParams(BaseModel):
    description: str = Field(
        default="",
        description="A short (3-5 word) description of the task",
    )
    prompt: str = Field(..., description="The task for the agent to perform")
    subagent_type: str = Field(
        default="general-purpose",
        description="The type of specialized agent to use for this task",
    )
    run_in_background: bool = Field(
        default=False,
        description="Run in background (returns immediately, notifies on completion)",
    )
    model: Literal["sonnet", "opus", "haiku", "inherit"] | None = Field(
        default=None,
        description=(
            "Optional model override for this agent. Takes precedence over the "
            "agent definition's model. If omitted, uses the agent definition's "
            "model, or inherits from the parent."
        ),
    )


class AgentTool(Tool):
    name = "Agent"
    description = "Launch a new agent to handle complex, multi-step tasks autonomously."
    prompt = AGENT_PROMPT
    kind = ToolKind.WRITE
    schema = AgentToolParams

    def __init__(self, config: Config):
        super().__init__(config)

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = AgentToolParams(**invocation.params)

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
        self,
        params: AgentToolParams,
        definition: AgentDefinition,
        invocation: ToolInvocation,
        agent_id: str | None = None,
    ) -> ToolResult:
        """Run agent synchronously and return result."""
        import uuid

        from agent.agent import Agent

        if agent_id is None:
            agent_id = str(uuid.uuid4())[:8]

        config_dict = self.config.to_dict()
        config_dict["max_turns"] = definition.max_turns

        from tools.registry import create_default_registry

        all_names = [t.name for t in create_default_registry(self.config).get_tools()]
        if definition.tools and definition.tools != ["*"]:
            base_allowed = list(definition.tools)
        elif definition.disallowed_tools:
            base_allowed = [
                n for n in all_names if n not in set(definition.disallowed_tools)
            ]
        else:
            base_allowed = list(all_names)
        config_dict["allowed_tools"] = [
            n for n in base_allowed if n not in _ALL_AGENT_DISALLOWED_TOOLS
        ]
        if params.model and params.model != "inherit":
            config_dict["model"] = {"model": params.model}

        sub_config = Config(**config_dict)

        extra_context = await self._fire_subagent_start(agent_id, definition)

        tool_calls = []
        last_text = ""

        from hooks.config import get_hooks_from_snapshot, swap_hooks_snapshot

        parent_snapshot = get_hooks_from_snapshot()

        try:
            async with Agent(sub_config) as agent:
                resolved = definition.resolve_system_prompt()
                if resolved:
                    cm = agent.session.context_manager
                    base = cm.system_prompt
                    cm.set_system_prompt(f"{base}\n\n{resolved}" if base else resolved)

                task_prompt = params.prompt
                if extra_context:
                    task_prompt = f"{extra_context}\n\n{task_prompt}"

                async for event in agent.run(task_prompt):
                    from agent.event import AgentEventType

                    if event.type == AgentEventType.TEXT_COMPLETE:
                        text = event.data.get("content", "")
                        if text:
                            last_text = text
                    elif event.type == AgentEventType.TOOL_CALL_START:
                        tool_calls.append(event.data.get("name", ""))
                    elif event.type == AgentEventType.AGENT_ERROR:
                        error = event.data.get("error", "")
                        if error:
                            last_text = f"Error: {error}"

        except Exception as e:
            swap_hooks_snapshot(parent_snapshot)
            await self._fire_subagent_stop(
                agent_id, definition, last_assistant_message=""
            )
            return ToolResult.error_result(f"Agent failed: {e}")

        swap_hooks_snapshot(parent_snapshot)

        await self._fire_subagent_stop(
            agent_id, definition, last_assistant_message=last_text
        )

        result_text = last_text or "Agent completed with no output."

        return ToolResult.success_result(
            result_text,
            metadata={"agent_type": definition.agent_type, "tools_called": tool_calls},
        )

    async def _run_background(
        self,
        params: AgentToolParams,
        definition: AgentDefinition,
        invocation: ToolInvocation,
    ) -> ToolResult:
        """Launch agent in background and return immediately."""
        import uuid

        from tools.builtin.shared.agentRegistry import (
            register_background_agent,
            set_background_result,
        )

        task_id = str(uuid.uuid4())[:8]

        async def _run():
            try:
                result = await self._run_foreground(
                    params, definition, invocation, agent_id=task_id
                )
                set_background_result(task_id, result.output)
            except Exception as e:
                set_background_result(task_id, f"Background agent failed: {e}")

        task = asyncio.create_task(_run())
        register_background_agent(task_id, definition.agent_type, task)

        return ToolResult.success_result(
            f"Agent '{definition.agent_type}' launched in background (task: {task_id}).\n"
            f"Retrieve its result with the AgentResult tool once it finishes.",
            metadata={"task_id": task_id, "background": True},
        )

    def _hook_executor(self):
        """Build a HookExecutor from the frozen session snapshot.

        These layers (subagent spawn) don't carry a HookExecutor reference, so
        we read the module-global snapshot the same way reaches its global
        hook config. Returns None when no hooks are configured (fast no-op).
        """
        from hooks.config import get_hooks_from_snapshot
        from hooks.executor import HookExecutor

        snapshot = get_hooks_from_snapshot()
        if not snapshot:
            return None
        return HookExecutor(snapshot)

    async def _fire_subagent_start(
        self, agent_id: str, definition: AgentDefinition
    ) -> str:
        """Fire SubagentStart; return any additional context to prepend to the task."""
        from core_types.hooks import HookEvent

        executor = self._hook_executor()
        if executor is None:
            return ""
        try:
            result = await executor.run_event_hooks(
                event=HookEvent.SUBAGENT_START.value,
                data={
                    "agent_id": agent_id,
                    "agent_type": definition.agent_type,
                    "cwd": str(self.config.cwd),
                },
            )
            return result.additional_context or ""
        except Exception:
            logger.debug("SubagentStart hook failed", exc_info=True)
            return ""

    async def _fire_subagent_stop(
        self, agent_id: str, definition: AgentDefinition, last_assistant_message: str
    ) -> None:
        from core_types.hooks import HookEvent

        executor = self._hook_executor()
        if executor is None:
            return
        try:
            await executor.run_event_hooks(
                event=HookEvent.SUBAGENT_STOP.value,
                data={
                    "agent_id": agent_id,
                    "agent_type": definition.agent_type,
                    "last_assistant_message": last_assistant_message,
                    "stop_hook_active": False,
                    "agent_transcript_path": "",
                    "cwd": str(self.config.cwd),
                },
            )
        except Exception:
            logger.debug("SubagentStop hook failed", exc_info=True)
