"""
Conversation hub — the chat area.

Responsibilities:
  - Own the Agent instance (async-context-managed).
  - Accept user input via @on(UserInputSubmitted).
  - Run the agent in a background worker, translating each AgentEvent
    into a Textual message posted to self so the UI can react.
  - Dispatch slash commands locally (same context bag as the REPL).
  - Mount child blocks: Welcome, UserInput, AgentResponse, AgentThought,
    ToolCall, Note.
  - Provide block-cursor navigation (Alt+↑/↓) across blocks.

The agent runs as a long-lived worker; user messages are queued via an
asyncio.Queue so we don't spawn a new task per turn (which would race the
Agent's internal state).
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalGroup, VerticalScroll
from textual.reactive import var
from textual.widget import Widget

from agent.agent import Agent
from agent.event import AgentEvent, AgentEventType
from commands import execute_slash_command, is_slash_command
from config.config import Config
from ui.messages import (
    AgentEnded,
    AgentFailed,
    AgentStarted,
    Flash,
    RequestExit,
    SlashCommandChosen,
    SlashResult,
    TextComplete,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    UserInputSubmitted,
)
from ui.widgets.agent_response import AgentResponse
from ui.widgets.agent_thought import AgentThought
from ui.widgets.flash import Flash as FlashWidget
from ui.widgets.note import Note
from ui.widgets.throbber import Throbber
from ui.widgets.tool_call import ToolCall
from ui.widgets.user_input import UserInput
from ui.widgets.welcome import Welcome


class Window(VerticalScroll):
    """Scrollable container that keeps new content anchored at the bottom."""

    DEFAULT_CSS = """
    Window {
        height: 1fr;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    """


class Conversation(VerticalGroup):
    DEFAULT_CSS = """
    Conversation {
        height: 1fr;
        layers: base float;

        Throbber { layer: base; dock: top; }
        Flash { layer: float; }

        /* Notes get semantic border colors */
        Note {
            padding: 0 1;
            margin: 0 1 1 1;
            color: $text-muted;
            border-left: blank $primary 30%;
            &.-error    { color: $text-error;    border-left: blank $error;   }
            &.-warning  { color: $text-warning;  border-left: blank $warning; }
            &.-stop-reason {
                color: $text-error;
                border-left: wide $error 60%;
                text-style: bold;
            }
        }

        /* Max-width column mode — centered, with a faint tint */
        &.-column {
            max-width: 100;
            background: black 7%;
        }
    }
    """

    BLOCK_BINDING_GROUP = Binding.Group(description="Block navigation")
    BINDINGS = [
        Binding(
            "alt+up", "cursor_up", "Block cursor up",
            group=BLOCK_BINDING_GROUP, show=True, priority=True,
        ),
        Binding(
            "alt+down", "cursor_down", "Block cursor down",
            group=BLOCK_BINDING_GROUP, show=True,
        ),
        Binding(
            "alt+home", "cursor_first", "First block",
            group=BLOCK_BINDING_GROUP, show=False,
        ),
        Binding(
            "alt+end", "cursor_last", "Last block",
            group=BLOCK_BINDING_GROUP, show=False,
        ),
    ]

    busy_count: var[int] = var(0)
    column: var[bool] = var(False, toggle_class="-column")

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self._agent: Agent | None = None
        self._agent_ready = asyncio.Event()
        self._input_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._current_response: AgentResponse | None = None
        self._current_thought: AgentThought | None = None
        self._tool_widgets: dict[str, ToolCall] = {}
        self._cursor_block: Widget | None = None
        self._welcome: Welcome | None = None

    # ─── Layout ───────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Throbber(id="throbber")
        yield Window(id="window")
        yield FlashWidget()

    async def on_mount(self) -> None:
        self._welcome = Welcome()
        await self._mount_block(self._welcome)
        self._run_agent_task()

    # ─── Agent worker (long-lived) ────────────────────────────────────

    @work(exclusive=True, group="agent")
    async def _run_agent_task(self) -> None:
        """Own the Agent for the screen's lifetime; feed it from the queue."""
        try:
            async with Agent(
                self.config, permission_prompt_fn=self._permission_prompt
            ) as agent:
                self._agent = agent
                self._agent_ready.set()
                await self._append_note(
                    f"Session {agent.session.session_id[:8]} · "
                    f"model: {self.config.model_name} · cwd: {self.config.cwd}"
                )
                self._update_status()
                while True:
                    message = await self._input_queue.get()
                    if message is None:
                        return
                    await self._run_one_turn(agent, message)
        except Exception as exc:  # pragma: no cover — defensive
            self.post_message(AgentFailed(error=str(exc)))
            await self._append_note(f"Agent crashed: {exc}", style="error")
        finally:
            self._agent_ready.set()

    # A turn = one user message processed end-to-end, 
    # regardless of how many LLM round-trips happen inside it.
    async def _run_one_turn(self, agent: Agent, message: str) -> None:
        self.post_message(AgentStarted())
        self._current_response = None
        try:
            async for event in agent.run(message):
                self._dispatch_agent_event(event)
        except Exception as exc:
            self.post_message(AgentFailed(error=str(exc)))
        finally:
            self.post_message(AgentEnded())
            self._update_status()

    def _dispatch_agent_event(self, event: AgentEvent) -> None:
        """Translate AgentEvent → Textual Message on the UI thread."""
        match event.type:
            case AgentEventType.TEXT_DELTA:
                self.post_message(TextDelta(content=event.data.get("content", "")))
            case AgentEventType.TEXT_COMPLETE:
                self.post_message(TextComplete(content=event.data.get("content", "")))
            case AgentEventType.TOOL_CALL_START:
                self.post_message(
                    ToolStarted(
                        call_id=event.data.get("call_id", ""),
                        name=event.data.get("name", ""),
                        arguments=event.data.get("arguments", {}),
                    )
                )
            case AgentEventType.TOOL_CALL_COMPLETE:
                self.post_message(
                    ToolCompleted(
                        call_id=event.data.get("call_id", ""),
                        name=event.data.get("name", ""),
                        success=bool(event.data.get("success", False)),
                        output=event.data.get("output") or "",
                        error=event.data.get("error"),
                        metadata=event.data.get("metadata") or {},
                        diff=event.data.get("diff"),
                        truncated=bool(event.data.get("truncated", False)),
                        exit_code=event.data.get("exit_code"),
                    )
                )
            case AgentEventType.AGENT_ERROR:
                self.post_message(AgentFailed(error=event.data.get("error", "unknown")))
            case _:
                pass  # AGENT_START / AGENT_END handled by our own turn wrapper

    # ─── Permission flow ──────────────────────────────────────────────

    async def _permission_prompt(self, tool_name: str, message: str, is_destructive: bool) -> bool:
        """Push the fullscreen PermissionsScreen and convert its 4-way decision
        back to a bool for the Agent's permission_prompt_fn signature.

        "allow_always" / "reject_always" are recorded on the active session
        rule set so we don't ask again for matching tool calls."""
        from ui.screens.permissions import PermissionsScreen

        decision = await self.app.push_screen_wait(
            PermissionsScreen(
                tool_name,
                message,
                is_destructive=is_destructive,
            )
        )
        if decision in ("allow_always", "reject_always"):
            self._remember_decision(tool_name, decision)
        return decision in ("allow_once", "allow_always")

    def _remember_decision(self, tool_name: str, decision: str) -> None:
        """Cache an always-decision for the current session.

        Best-effort: if the agent's permission rules don't expose a setter we
        still showed the right UI; the user just gets re-prompted next time.
        """
        try:
            rules = getattr(self._agent.session, "permission_rules", None) if self._agent else None
            if rules is None:
                return
            if decision == "allow_always" and hasattr(rules, "allow"):
                rules.allow(tool_name)
            elif decision == "reject_always" and hasattr(rules, "deny"):
                rules.deny(tool_name)
        except Exception:
            pass

    # ─── Input & slash commands ───────────────────────────────────────

    @on(UserInputSubmitted)
    async def _on_user_submitted(self, event: UserInputSubmitted) -> None:
        event.stop()
        await self._dismiss_welcome_once()
        text = event.body
        await self._mount_block(UserInput(text))

        if is_slash_command(text):
            await self._run_slash(text)
            return
        await self._input_queue.put(text)

    @on(SlashCommandChosen)
    async def _on_slash_chosen(self, event: SlashCommandChosen) -> None:
        event.stop()
        await self._dismiss_welcome_once()
        await self._mount_block(UserInput(event.command))
        await self._run_slash(event.command)

    async def _run_slash(self, text: str) -> None:
        try:
            await asyncio.wait_for(self._agent_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pass

        if self._agent is None:
            await self._append_note(
                "Agent is not available — slash commands need a running session.",
                style="error",
            )
            return

        ctx = {
            "config": self.config,
            "session": self._agent.session,
            "context_manager": self._agent.session.context_manager,
        }
        try:
            result = await execute_slash_command(text, ctx)
        except Exception as exc:
            await self._append_note(f"Command error: {exc}", style="error")
            return

        if result == "__EXIT__":
            self.post_message(RequestExit())
            return
        if isinstance(result, str) and result.startswith("__RETRY__:"):
            retry_msg = result[len("__RETRY__:") :]
            await self._input_queue.put(retry_msg)
            return
        if result:
            self.post_message(SlashResult(text=result))
        self._update_status()

    @on(SlashResult)
    async def _on_slash_result(self, event: SlashResult) -> None:
        event.stop()
        await self._mount_block(Note(event.text))

    # ─── Agent event handlers ─────────────────────────────────────────

    @on(AgentStarted)
    def _on_started(self, event: AgentStarted) -> None:
        event.stop()
        self.busy_count += 1

    @on(AgentEnded)
    def _on_ended(self, event: AgentEnded) -> None:
        event.stop()
        self.busy_count = max(0, self.busy_count - 1)
        self._current_response = None
        self._current_thought = None

    @on(AgentFailed)
    async def _on_failed(self, event: AgentFailed) -> None:
        event.stop()
        await self._append_note(f"Error: {event.error}", style="error")

    @on(TextDelta)
    async def _on_text_delta(self, event: TextDelta) -> None:
        event.stop()
        await self._dismiss_welcome_once()
        if self._current_response is None:
            self._current_response = AgentResponse()
            await self._mount_block(self._current_response)
        await self._current_response.append_fragment(event.content)

    @on(TextComplete)
    async def _on_text_complete(self, event: TextComplete) -> None:
        event.stop()
        self._current_response = None

    @on(ToolStarted)
    async def _on_tool_started(self, event: ToolStarted) -> None:
        event.stop()
        await self._dismiss_welcome_once()
        # New tool call closes streaming blocks
        self._current_response = None
        self._current_thought = None
        widget = ToolCall(event.call_id, event.name, event.arguments)
        self._tool_widgets[event.call_id] = widget
        await self._mount_block(widget)

    @on(ToolCompleted)
    async def _on_tool_completed(self, event: ToolCompleted) -> None:
        event.stop()
        widget = self._tool_widgets.pop(event.call_id, None)
        if widget is None:
            return
        await widget.complete(
            success=event.success,
            output=event.output,
            error=event.error,
            diff=event.diff,
            metadata=event.metadata,
            exit_code=event.exit_code,
            truncated=event.truncated,
        )

    @on(Flash)
    def _on_flash(self, event: Flash) -> None:
        event.stop()
        self.query_one(FlashWidget).flash(
            event.content,
            style=event.style,
            duration=event.duration or 3.0,
        )

    # ─── Reactive housekeeping ────────────────────────────────────────

    def watch_busy_count(self, count: int) -> None:
        self.query_one(Throbber).set_busy(count > 0)

    # ─── Helpers ──────────────────────────────────────────────────────

    async def _mount_block(self, widget: Any) -> None:
        window = self.query_one(Window)
        await window.mount(widget)
        window.scroll_end(animate=False)

    async def _append_note(self, text: str, *, style: str = "default") -> None:
        note = Note(text)
        if style == "error":
            note.add_class("-error")
        elif style == "warning":
            note.add_class("-warning")
        await self._mount_block(note)

    async def _dismiss_welcome_once(self) -> None:
        """Remove the welcome block the first time real content arrives."""
        if self._welcome is not None:
            try:
                await self._welcome.remove()
            except Exception:
                pass
            self._welcome = None

    def _update_status(self) -> None:
        """Ask the screen's Prompt to refresh its status line from token stats."""
        if self._agent is None:
            return
        try:
            stats = self._agent.session.context_manager.get_token_stats()
        except Exception:
            return
        # Walk up to MainScreen, find the Prompt, ask it to render the stats.
        try:
            from ui.widgets.prompt import Prompt
            from ui.widgets.status_line import StatusLine
            prompt = self.screen.query_one(Prompt)
            prompt.query_one(StatusLine).set_token_stats(stats)
        except Exception:
            pass

    # ─── Block cursor navigation ──────────────────────────────────────

    def _iter_blocks(self) -> list[Widget]:
        try:
            window = self.query_one(Window)
        except Exception:
            return []
        return [w for w in window.query("*") if w.parent is window]

    def action_cursor_up(self) -> None:
        blocks = self._iter_blocks()
        if not blocks:
            return
        if self._cursor_block is None or self._cursor_block not in blocks:
            self._focus_block(blocks[-1])
            return
        idx = blocks.index(self._cursor_block)
        if idx > 0:
            self._focus_block(blocks[idx - 1])

    def action_cursor_down(self) -> None:
        blocks = self._iter_blocks()
        if not blocks:
            return
        if self._cursor_block is None or self._cursor_block not in blocks:
            self._focus_block(blocks[0])
            return
        idx = blocks.index(self._cursor_block)
        if idx < len(blocks) - 1:
            self._focus_block(blocks[idx + 1])

    def action_cursor_first(self) -> None:
        blocks = self._iter_blocks()
        if blocks:
            self._focus_block(blocks[0])

    def action_cursor_last(self) -> None:
        blocks = self._iter_blocks()
        if blocks:
            self._focus_block(blocks[-1])

    def _focus_block(self, widget: Widget) -> None:
        self._cursor_block = widget
        # Highlight the block by scrolling to it
        try:
            widget.scroll_visible(animate=False)
        except Exception:
            pass
        # Try to focus — Markdown / Static children may not be focusable;
        # that's fine, the cursor still tracks logically.
        try:
            if widget.can_focus:
                widget.focus()
        except Exception:
            pass

    # ─── Shutdown ─────────────────────────────────────────────────────

    async def on_unmount(self) -> None:
        await self._input_queue.put(None)

    def abort_current(self) -> None:
        if self._agent is not None:
            self._agent.abort()
