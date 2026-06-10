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
from typing import Literal

from pydantic import BaseModel, Field

from config.config import Config
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
# Per-agent definitions live in tools/builtin/AgentTool/built_in/, mirroring
# Claude Code's src/tools/AgentTool/built-in/ directory (hyphen → underscore
# for Python module compatibility).
from tools.builtin.AgentTool.built_in import BUILT_IN_AGENTS
from tools.builtin.AgentTool.built_in_types import AgentDefinition
from tools.builtin.AgentTool.prompt import AGENT_PROMPT

logger = logging.getLogger(__name__)


class AgentToolParams(BaseModel):
    # Field order/wording mirrors CC AgentTool.tsx inputSchema.
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
    # CC constrains model to the three tiers (plus 'inherit' handled in code).
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
    kind = ToolKind.WRITE  # Can modify files via sub-agent
    schema = AgentToolParams

    def __init__(self, config: Config):
        super().__init__(config)

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
        self, params: AgentToolParams, definition: AgentDefinition, invocation: ToolInvocation,
        agent_id: str | None = None,
    ) -> ToolResult:
        """Run agent synchronously and return result."""
        import uuid

        from agent.agent import Agent

        if agent_id is None:
            agent_id = str(uuid.uuid4())[:8]

        # Build sub-config with restricted tools. `tools` is the allowlist;
        # for agents that declare only disallowed_tools (e.g. Explore/Plan block
        # the write tools), compute the allowlist as "everything except those".
        config_dict = self.config.to_dict()
        config_dict["max_turns"] = definition.max_turns
        if definition.tools and definition.tools != ["*"]:
            config_dict["allowed_tools"] = definition.tools
        elif definition.disallowed_tools:
            from tools.registry import create_default_registry
            all_names = [t.name for t in create_default_registry(self.config).get_tools()]
            config_dict["allowed_tools"] = [
                n for n in all_names if n not in set(definition.disallowed_tools)
            ]
        if params.model and params.model != "inherit":
            config_dict["model"] = {"model": params.model}

        sub_config = Config(**config_dict)

        # SubagentStart hook — may inject additional context that gets prepended
        # to the subagent's task (CC: runAgent.ts:532, additionalContexts → user message).
        extra_context = await self._fire_subagent_start(agent_id, definition)

        result_parts = []
        tool_calls = []
        last_text = ""

        # A nested Agent's __aenter__ calls capture_hooks_snapshot(), which would
        # clobber the parent session's frozen hooks for the rest of the run.
        # Save the parent snapshot now and restore it after the sub-agent exits,
        # so SubagentStop and the parent's later turns see the right hooks.
        from hooks.config import get_hooks_from_snapshot, swap_hooks_snapshot
        parent_snapshot = get_hooks_from_snapshot()

        try:
            async with Agent(sub_config) as agent:
                # CC parity: the agent's specialized instructions are its SYSTEM
                # prompt, not f-string framing on the task. system_prompt may be a
                # callable (most built-in agents) — resolve_system_prompt() handles
                # both; the raw attribute would render as "<function ...>".
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
                            result_parts.append(text)
                            last_text = text
                    elif event.type == AgentEventType.TOOL_CALL_START:
                        tool_calls.append(event.data.get("name", ""))
                    elif event.type == AgentEventType.AGENT_ERROR:
                        error = event.data.get("error", "")
                        if error:
                            result_parts.append(f"Error: {error}")

        except Exception as e:
            swap_hooks_snapshot(parent_snapshot)  # restore before firing stop
            await self._fire_subagent_stop(agent_id, definition, last_assistant_message="")
            return ToolResult.error_result(f"Agent failed: {e}")

        # Restore parent hooks before SubagentStop so the stop hook (and the
        # parent's subsequent turns) see the parent session's configuration.
        swap_hooks_snapshot(parent_snapshot)

        # SubagentStop hook (CC: stopHooks.ts, isAgent=true → SubagentStop).
        await self._fire_subagent_stop(agent_id, definition, last_assistant_message=last_text)

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

        from tools.builtin.shared.agentRegistry import (
            register_background_agent,
            set_background_result,
        )

        task_id = str(uuid.uuid4())[:8]

        async def _run():
            try:
                result = await self._run_foreground(params, definition, invocation, agent_id=task_id)
                set_background_result(task_id, result.output)
            except Exception as e:  # never let a background task die silently
                set_background_result(task_id, f"Background agent failed: {e}")

        task = asyncio.create_task(_run())
        register_background_agent(task_id, definition.agent_type, task)

        return ToolResult.success_result(
            f"Agent '{definition.agent_type}' launched in background (task: {task_id}).\n"
            f"Retrieve its result with the AgentResult tool once it finishes.",
            metadata={"task_id": task_id, "background": True},
        )

    # ─── Lifecycle hooks (CC parity) ──────────────────────────────────

    def _hook_executor(self):
        """Build a HookExecutor from the frozen session snapshot.

        These layers (subagent spawn) don't carry a HookExecutor reference, so
        we read the module-global snapshot the same way CC reaches its global
        hook config. Returns None when no hooks are configured (fast no-op).
        """
        from hooks.config import get_hooks_from_snapshot
        from hooks.executor import HookExecutor

        snapshot = get_hooks_from_snapshot()
        if not snapshot:
            return None
        return HookExecutor(snapshot)

    async def _fire_subagent_start(self, agent_id: str, definition: AgentDefinition) -> str:
        """Fire SubagentStart; return any additional context to prepend to the task."""
        from core_types.hooks import HookEvent

        executor = self._hook_executor()
        if executor is None:
            return ""
        try:
            result = await executor.run_event_hooks(
                event=HookEvent.SUBAGENT_START.value,
                data={"agent_id": agent_id, "agent_type": definition.agent_type,
                      "cwd": str(self.config.cwd)},
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
                data={"agent_id": agent_id, "agent_type": definition.agent_type,
                      "last_assistant_message": last_assistant_message,
                      "cwd": str(self.config.cwd)},
            )
        except Exception:
            logger.debug("SubagentStop hook failed", exc_info=True)
