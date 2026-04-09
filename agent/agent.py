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
        self._hook_executor: HookExecutor | None = None

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
        if self._hook_executor:
            await self._hook_executor.run_event_hooks(
                event=HookEvent.USER_PROMPT_SUBMIT,
                data={"message": message, "session_id": self.session.session_id},
            )

        async for event in query_loop(
            self.session,
            self.config,
            permission_prompt_fn=self._permission_prompt_fn,
            abort_controller=self._abort_controller,
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

        # Load hooks from 5-layer settings (Claude Code: settings.json hooks section)
        from config.settings import extract_hooks_from_settings, load_settings
        settings = load_settings(str(self.config.cwd))
        hooks_config = extract_hooks_from_settings(settings)

        # Capture hooks snapshot at startup (Claude Code: immutable at startup)
        capture_hooks_snapshot(hooks_config if hooks_config else None)
        self._hook_executor = HookExecutor(get_hooks_from_snapshot())

        # Fire SessionStart hook
        if self._hook_executor:
            await self._hook_executor.run_event_hooks(
                event=HookEvent.SESSION_START,
                data={
                    "session_id": self.session.session_id,
                    "cwd": str(self.config.cwd),
                    "model": self.config.model_name,
                },
            )

        # Fire InstructionsLoaded hook for each AGENT.md file
        from context.instructions import load_all_instructions
        instructions = load_all_instructions(str(self.config.cwd))
        if self._hook_executor and instructions:
            for instr in instructions:
                await self._hook_executor.run_event_hooks(
                    event=HookEvent.INSTRUCTIONS_LOADED,
                    data={
                        "file_path": instr.path,
                        "memory_type": instr.type,
                        "load_reason": "initial_load",
                    },
                )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # Fire SessionEnd hook
        if self._hook_executor:
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
                pass  # SessionEnd hooks must not prevent cleanup

        # Save final metadata
        if self.session:
            self.session._save_metadata()

        if self.session and self.session.client:
            await self.session.client.close()
        if self.session and self.session.mcp_manager:
            await self.session.mcp_manager.shutdown()
        self.session = None
