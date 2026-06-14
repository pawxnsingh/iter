"""
Agent: Owns the query loop lifecycle.

The Agent is thin — the real logic is in
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
        self._hook_executor: HookExecutor = HookExecutor()
        self._user_context_reminder: str = ""

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

        prompt_hook = await self._hook_executor.run_event_hooks(
            event=HookEvent.USER_PROMPT_SUBMIT,
            data={
                "prompt": message,
                "session_id": self.session.session_id,
                "cwd": str(self.config.cwd),
            },
        )

        if prompt_hook.blocking_error:
            yield AgentEvent.text_complete(
                content=f"{prompt_hook.blocking_error}\n\nOriginal prompt: {message}"
            )
            self._abort_controller = None
            yield AgentEvent.agent_end()
            return

        self.session.context_manager.add_user_message(content=message)
        self.session.save_message(role="user", content=message)

        if prompt_hook.additional_context:
            ctx_text = prompt_hook.additional_context[:10000]
            ctx = (
                "<system-reminder>\n"
                f"UserPromptSubmit hook additional context: {ctx_text}\n"
                "</system-reminder>"
            )
            self.session.context_manager.add_user_message(content=ctx)
            self.session.save_message(role="user", content=ctx)

        if prompt_hook.prevent_continuation:
            stop_msg = "Operation stopped by hook"
            self.session.context_manager.add_user_message(content=stop_msg)
            self.session.save_message(role="user", content=stop_msg)
            yield AgentEvent.text_complete(content=stop_msg)
            self._abort_controller = None
            yield AgentEvent.agent_end()
            return

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

        compact_fn = create_compact_fn(
            client=self.session.client,
            model=self.config.model_name,
        )
        self.session.context_manager.set_compact_fn(compact_fn)
        self.session.context_manager.set_summary_sink(self.session.save_summary)
        self.session.context_manager.set_transcript_path(self.session.transcript_path)

        from config.settings import (
            apply_settings_env,
            extract_hooks_from_settings,
            extract_permission_rules,
            get_default_permission_mode,
            load_settings,
        )
        settings = load_settings(str(self.config.cwd))

        apply_settings_env(settings)

        from core_types.permissions import PermissionMode
        default_mode = get_default_permission_mode(settings)
        if default_mode and self.config.permission_mode == PermissionMode.DEFAULT:
            self.config.permission_mode = default_mode

        hooks_config = extract_hooks_from_settings(settings)

        capture_hooks_snapshot(hooks_config if hooks_config else None)
        self._hook_executor = HookExecutor(get_hooks_from_snapshot())
        from core_types.permissions import permission_mode_external
        transcript_path = ""
        if getattr(self.session, "storage", None) is not None:
            transcript_path = str(self.session.storage.path)
        self._hook_executor.set_base_context(
            session_id=self.session.session_id,
            transcript_path=transcript_path,
            cwd=str(self.config.cwd),
            permission_mode=permission_mode_external(self.config.permission_mode),
        )

        rules = extract_permission_rules(settings)
        if not self.config.allow_rules:
            self.config.allow_rules = rules.get("allow", {})
        if not self.config.deny_rules:
            self.config.deny_rules = rules.get("deny", {})
        if not self.config.ask_rules:
            self.config.ask_rules = rules.get("ask", {})

        session_start = await self._hook_executor.run_event_hooks(
            event=HookEvent.SESSION_START,
            data={
                "session_id": self.session.session_id,
                "source": "startup",
                "cwd": str(self.config.cwd),
                "model": self.config.model_name,
            },
        )
        if session_start.additional_context:
            self.session.context_manager.add_user_message(
                f"<session-start-hook>\n{session_start.additional_context}\n</session-start-hook>"
            )

        from context.instructions import (
            build_system_context_suffix,
            build_user_context_reminder,
            load_all_instructions,
        )
        instructions = load_all_instructions(str(self.config.cwd))
        _MEMORY_TYPE = {"managed": "Managed", "user": "User",
                        "project": "Project", "local": "Local"}
        for instr in instructions:
            await self._hook_executor.run_event_hooks(
                event=HookEvent.INSTRUCTIONS_LOADED,
                data={
                    "file_path": instr.path,
                    "memory_type": _MEMORY_TYPE.get(instr.type, instr.type),
                    "load_reason": "session_start",
                },
            )
        import datetime as _dt
        self._user_context_reminder = build_user_context_reminder(
            str(self.config.cwd), _dt.date.today().isoformat()
        )
        self.session.context_manager.set_user_context_reminder(
            self._user_context_reminder
        )
        self.session.context_manager.set_system_context_suffix(
            build_system_context_suffix(str(self.config.cwd))
        )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            await self._hook_executor.run_event_hooks(
                event=HookEvent.SESSION_END,
                data={
                    "session_id": self.session.session_id if self.session else "",
                    "reason": "other",
                },
            )
        except Exception:
            pass

        if self.session:
            self.session._save_metadata()

        if self.session and self.session.client:
            await self.session.client.close()
        if self.session and self.session.mcp_manager:
            await self.session.mcp_manager.shutdown()
        self.session = None
