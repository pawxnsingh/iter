"""
Agent: Owns the query loop lifecycle.

Claude Code equivalent: The Agent is thin — the real logic is in
query_loop() (query/loop.py). Agent owns startup/shutdown and
wires components together.

Lifecycle hooks fired:
  __aenter__  → SessionStart hooks
  run()       → query_loop (PreToolUse/PostToolUse hooks inside)
  __aexit__   → SessionEnd hooks
"""

from __future__ import annotations

import logging

from agent.event import AgentEvent
from agent.session import Session
from config.config import Config
from core_types.hooks import HookEvent
from hooks.config import capture_hooks_snapshot, get_hooks_from_snapshot
from hooks.executor import HookExecutor
from query.loop import query_loop
from services.compact.compact import create_compact_fn
from utils.abort import AbortController

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, config: Config, permission_prompt_fn=None):
        self.config = config
        self.session: Session | None = Session(config=config)
        self._permission_prompt_fn = permission_prompt_fn
        self._abort_controller: AbortController | None = None
        # Start with an empty HookExecutor so callers don't need a None-guard
        # before every fire site. Replaced with a real one in __aenter__ once
        # we've snapshotted the hooks config.
        self._hook_executor: HookExecutor = HookExecutor()

    def abort(self):
        """Cancel the current operation. Called from Ctrl+C handler."""
        if self._abort_controller:
            self._abort_controller.abort("interrupt")

    async def run(self, message: str):
        """
        Process a user message through the query loop.
        Yields AgentEvents for the UI to render in real-time.
        """
        self._abort_controller = AbortController()

        yield AgentEvent.agent_start(message)
        self.session.context_manager.add_user_message(content=message)
        self.session.save_message(role="user", content=message)

        # Fire UserPromptSubmit hook
        await self._hook_executor.run_event_hooks(
            event=HookEvent.USER_PROMPT_SUBMIT,
            data={"message": message, "session_id": self.session.session_id},
        )

        async for event in query_loop(
            self.session,
            self.config,
            permission_prompt_fn=self._permission_prompt_fn,
            abort_controller=self._abort_controller,
            hook_executor=self._hook_executor,
        ):
            yield event

        self._abort_controller = None
        yield AgentEvent.agent_end()

    async def __aenter__(self) -> Agent:
        await self.session.initialize()

        # Inject compact function (Claude Code: QueryDeps pattern)
        compact_fn = create_compact_fn(
            client=self.session.client,
            model=self.config.model_name,
        )
        self.session.context_manager.set_compact_fn(compact_fn)

        # Load settings (5-layer merge) once: hooks + permission rules + ...
        from config.settings import (
            extract_hooks_from_settings,
            extract_permission_rules,
            load_settings,
        )
        settings = load_settings(str(self.config.cwd))
        hooks_config = extract_hooks_from_settings(settings)

        # Capture hooks snapshot at startup (Claude Code: immutable at startup)
        capture_hooks_snapshot(hooks_config if hooks_config else None)
        self._hook_executor = HookExecutor(get_hooks_from_snapshot())

        # Permission rules from settings.json → runtime context. CC parity:
        # `allow`/`deny`/`ask` are lists of "Tool(content)" strings; parser
        # bucketizes them by tool name. Only overlay onto Config if the
        # caller hasn't already set rules explicitly.
        rules = extract_permission_rules(settings)
        if not self.config.allow_rules:
            self.config.allow_rules = rules.get("allow", {})
        if not self.config.deny_rules:
            self.config.deny_rules = rules.get("deny", {})
        if not self.config.ask_rules:
            self.config.ask_rules = rules.get("ask", {})

        # Fire SessionStart hook
        await self._hook_executor.run_event_hooks(
            event=HookEvent.SESSION_START,
            data={
                "session_id": self.session.session_id,
                "cwd": str(self.config.cwd),
                "model": self.config.model_name,
            },
        )

        # Load the full instruction hierarchy (managed → user → walk-up project
        # AGENT.md → .ai-agent/rules/*.md → local), fire InstructionsLoaded for
        # each, AND fold the combined text into the system prompt. CC includes
        # every CLAUDE.md tier in context; previously only a single cwd AGENT.md
        # (config.developer_instructions) reached the model.
        from context.instructions import (
            format_instructions_for_prompt,
            load_all_instructions,
        )
        instructions = load_all_instructions(str(self.config.cwd))
        for instr in instructions:
            await self._hook_executor.run_event_hooks(
                event=HookEvent.INSTRUCTIONS_LOADED,
                data={
                    "file_path": instr.path,
                    "memory_type": instr.type,
                    "load_reason": "initial_load",
                },
            )
        if instructions:
            cm = self.session.context_manager
            base = cm.system_prompt or ""
            # Only fold in tiers whose content isn't already in the prompt — the
            # cwd AGENT.md is typically already present via developer_instructions;
            # this adds the rest (managed, user, parent-dir, rules, local).
            extra = [
                i for i in instructions
                if i.content and i.content.strip() and i.content.strip() not in base
            ]
            if extra:
                combined = format_instructions_for_prompt(extra)
                cm.set_system_prompt(
                    f"{base}\n\n# Project & User Instructions\n\n{combined}"
                    if base else combined
                )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # Fire SessionEnd hook — best-effort, must not prevent cleanup
        try:
            await self._hook_executor.run_event_hooks(
                event=HookEvent.SESSION_END,
                data={
                    "session_id": self.session.session_id if self.session else "",
                    "exit_reason": "error" if exc_type else "normal",
                    "turn_count": self.session._turn_count if self.session else 0,
                },
            )
        except Exception:
            pass

        # Save final metadata
        if self.session:
            self.session._save_metadata()

        if self.session and self.session.client:
            await self.session.client.close()
        if self.session and self.session.mcp_manager:
            await self.session.mcp_manager.shutdown()
        self.session = None
