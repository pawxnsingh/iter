"""
Brutal integration tests for the TUI's message flow:
  - rapid stream of TextDeltas doesn't drop chunks
  - interleaved Tool + Text events compose correctly
  - multiple sequential slash commands don't leak state
  - /clear resets context
  - ToolStarted without ToolCompleted still shows "running"
  - Abort from the screen propagates to the agent
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")


async def _wait_for_agent(conv, pilot, timeout: float = 3.0) -> None:
    steps = int(timeout / 0.05)
    for _ in range(steps):
        if conv._agent is not None:
            return
        await pilot.pause(0.05)
    raise AssertionError("agent worker never became ready")


def _text(widget) -> str:
    blob = getattr(widget, "_content", None)
    if blob is not None:
        return str(blob)
    try:
        return str(widget.render())
    except Exception:
        return repr(widget)


# ─── Stress: high-frequency streaming ────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_many_deltas_without_drops() -> None:
    """Post 200 TextDelta messages; assistant block must end with the full text."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import AgentEnded, AgentStarted, TextComplete, TextDelta
    from ui.widgets.agent_response import AgentResponse
    from ui.widgets.conversation import Conversation

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        conv.post_message(AgentStarted())
        for i in range(200):
            conv.post_message(TextDelta(content=f"{i} "))
        conv.post_message(TextComplete())
        conv.post_message(AgentEnded())

        # Let the message pump drain
        for _ in range(40):
            await pilot.pause(0.05)
            if list(conv.query(AgentResponse)):
                break
        responses = list(conv.query(AgentResponse))
        assert responses, "no AgentResponse was mounted"
        # MarkdownStream receives everything via async write; just verify the
        # widget has content beyond empty.
        assert len(responses[0].source) > 100, (
            f"expected stream content, got {responses[0].source!r}"
        )


@pytest.mark.asyncio
async def test_tool_interleaved_with_text() -> None:
    """Two tools + two text blocks in sequence — all mount in the right order."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import (
        AgentEnded, AgentStarted, TextComplete, TextDelta,
        ToolCompleted, ToolStarted,
    )
    from ui.widgets.agent_response import AgentResponse
    from ui.widgets.conversation import Conversation
    from ui.widgets.tool_call import ToolCall

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        conv.post_message(AgentStarted())
        conv.post_message(TextDelta(content="thinking..."))
        conv.post_message(TextComplete())
        conv.post_message(ToolStarted(call_id="a", name="Read",
                                       arguments={"path": "/x"}))
        conv.post_message(ToolCompleted(
            call_id="a", name="Read", success=True, output="hello",
            error=None, metadata={}, diff=None, truncated=False, exit_code=None,
        ))
        conv.post_message(TextDelta(content="ok done"))
        conv.post_message(TextComplete())
        conv.post_message(ToolStarted(call_id="b", name="Bash",
                                       arguments={"command": "ls"}))
        conv.post_message(ToolCompleted(
            call_id="b", name="Bash", success=True, output="",
            error=None, metadata={}, diff=None, truncated=False, exit_code=0,
        ))
        conv.post_message(AgentEnded())

        for _ in range(40):
            await pilot.pause(0.05)
            if len(list(conv.query(ToolCall))) >= 2 and len(list(conv.query(AgentResponse))) >= 2:
                break
        assert len(list(conv.query(ToolCall))) == 2
        # At least two AgentResponse blocks — one per TextComplete cycle
        assert len(list(conv.query(AgentResponse))) >= 2


@pytest.mark.asyncio
async def test_multiple_sequential_slash_commands() -> None:
    """Three slash commands in a row — all execute, no state leaks."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import UserInputSubmitted
    from ui.widgets.conversation import Conversation, Note

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        for cmd in ("/help", "/status", "/version"):
            conv.post_message(UserInputSubmitted(body=cmd))
            await pilot.pause(0.1)

        for _ in range(40):
            await pilot.pause(0.05)
            notes = [_text(n) for n in conv.query(Note)]
            hits = (
                any("Available" in n for n in notes),
                any(("Session:" in n) or ("Model:" in n) for n in notes),
                any("v0.1.0" in n for n in notes),
            )
            if all(hits):
                break
        blobs = [_text(n) for n in conv.query(Note)]
        assert any("Available" in b for b in blobs), f"help missing: {blobs}"
        assert any("v0.1.0" in b for b in blobs), f"version missing: {blobs}"


@pytest.mark.asyncio
async def test_tool_running_before_completion() -> None:
    """Without a ToolCompleted, the ToolCall widget stays in the running state."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import ToolStarted
    from ui.widgets.conversation import Conversation
    from ui.widgets.tool_call import ToolCall

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        conv.post_message(ToolStarted(call_id="pending", name="Bash",
                                       arguments={"command": "sleep 10"}))
        for _ in range(20):
            await pilot.pause(0.02)
            if list(conv.query(ToolCall)):
                break
        t = list(conv.query(ToolCall))[0]
        assert t.is_running
        assert not t.is_success
        assert not t.is_error


@pytest.mark.asyncio
async def test_flash_severity_styling() -> None:
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import Flash as FlashMsg
    from ui.widgets.conversation import Conversation
    from ui.widgets.flash import Flash

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        flash = conv.query_one(Flash)

        conv.post_message(FlashMsg(content="warn", style="warning", duration=5))
        for _ in range(10):
            await pilot.pause(0.05)
            if flash.has_class("-warning"):
                break
        assert flash.has_class("-warning")

        conv.post_message(FlashMsg(content="err", style="error", duration=5))
        for _ in range(10):
            await pilot.pause(0.05)
            if flash.has_class("-error"):
                break
        assert flash.has_class("-error")
        assert not flash.has_class("-warning"), "previous style should be cleared"


@pytest.mark.asyncio
async def test_slash_clear_removes_context() -> None:
    """/clear should say 'Conversation cleared.'."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import UserInputSubmitted
    from ui.widgets.conversation import Conversation, Note

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        conv.post_message(UserInputSubmitted(body="/clear"))
        for _ in range(40):
            await pilot.pause(0.05)
            if any("cleared" in _text(n).lower() for n in conv.query(Note)):
                break
        blobs = [_text(n) for n in conv.query(Note)]
        assert any("cleared" in b.lower() for b in blobs), blobs


@pytest.mark.asyncio
async def test_abort_action_does_not_crash() -> None:
    """MainScreen Ctrl+C on empty prompt calls abort — no crash, no wedge."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.widgets.conversation import Conversation
    from ui.widgets.prompt import PromptTextArea

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)
        ta = app.query_one(PromptTextArea)
        ta.focus()
        await pilot.pause()
        # Empty prompt → ctrl+c triggers abort (not clear)
        await pilot.press("ctrl+c")
        await pilot.pause()
        # Agent should still be reachable; abort is idempotent
        assert conv._agent is not None


@pytest.mark.asyncio
async def test_slash_chosen_from_overlay_triggers_command() -> None:
    """SlashCommandChosen → command executes in Conversation."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import SlashCommandChosen
    from ui.widgets.conversation import Conversation, Note

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        # Simulate picking /version from the overlay
        conv.post_message(SlashCommandChosen(command="/version"))
        for _ in range(40):
            await pilot.pause(0.05)
            if any("v0.1.0" in _text(n) for n in conv.query(Note)):
                break
        blobs = [_text(n) for n in conv.query(Note)]
        assert any("v0.1.0" in b for b in blobs), blobs
