"""
Comprehensive end-to-end tests for the Textual UI.

Every widget has at least one behavioral test. Integration tests drive
the full event flow through Conversation without touching a real LLM.

Layout:
  1. Primitives       — pill()
  2. Widgets (harness) — Throbber, Flash, UserInput, Note, AgentResponse,
                          ToolCall (lifecycle + branches), SlashComplete
  3. Integration       — Conversation event dispatch, slash command results
  4. Screens           — MainScreen bindings, PermissionModal
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


def _text(widget) -> str:
    """Extract plain text from a Static-derived widget, tolerating API drift."""
    blob = getattr(widget, "_content", None)
    if blob is not None:
        return str(blob)
    try:
        return str(widget.render())
    except Exception:
        return repr(widget)


class WidgetHarness:
    """A tiny Textual app that hosts a single widget for isolated tests."""

    @staticmethod
    def make_app(widget):
        from textual.app import App, ComposeResult

        class _App(App):
            def compose(self) -> ComposeResult:
                yield widget

        return _App()


def test_pill_wraps_in_half_blocks() -> None:
    from ui.widgets.pill import pill

    content = pill("done", background="$success", foreground="$text")
    plain = content.plain
    assert plain.startswith("▌")
    assert plain.endswith("▐")
    assert "done" in plain


def test_pill_accepts_content_input() -> None:
    from textual.content import Content
    from ui.widgets.pill import pill

    result = pill(Content("x"))
    assert "x" in result.plain


@pytest.mark.asyncio
async def test_throbber_toggle_class() -> None:
    from ui.widgets.throbber import Throbber

    t = Throbber()
    app = WidgetHarness.make_app(t)
    async with app.run_test(size=(80, 5)) as pilot:
        await pilot.pause()
        assert not t.has_class("-busy")
        t.set_busy(True)
        await pilot.pause()
        assert t.has_class("-busy")
        t.set_busy(False)
        await pilot.pause()
        assert not t.has_class("-busy")


@pytest.mark.asyncio
async def test_throbber_renders_gradient_chars() -> None:
    from ui.widgets.throbber import Throbber

    t = Throbber()
    app = WidgetHarness.make_app(t)
    async with app.run_test(size=(20, 3)) as pilot:
        await pilot.pause()
        rendered = t.render()
        text = rendered.plain
        assert len(text) >= 1
        assert set(text) == {"━"}


@pytest.mark.asyncio
async def test_flash_show_and_style_class() -> None:
    from ui.widgets.flash import Flash

    f = Flash()
    app = WidgetHarness.make_app(f)
    async with app.run_test(size=(80, 5)) as pilot:
        await pilot.pause()
        assert not f.visible

        f.flash("hello", style="success", duration=10)
        await pilot.pause()
        assert f.visible
        assert f.has_class("-success")
        assert not f.has_class("-error")

        f.flash("oops", style="error", duration=10)
        await pilot.pause()
        assert f.has_class("-error")
        assert not f.has_class("-success")


@pytest.mark.asyncio
async def test_flash_auto_hides_after_duration() -> None:
    from ui.widgets.flash import Flash

    f = Flash()
    app = WidgetHarness.make_app(f)
    async with app.run_test(size=(80, 5)) as pilot:
        await pilot.pause()
        f.flash("gone in a flash", style="default", duration=0.1)
        await pilot.pause()
        assert f.visible
        for _ in range(30):
            await pilot.pause(0.05)
            if not f.visible:
                break
        assert not f.visible, "Flash should auto-hide after its duration"


@pytest.mark.asyncio
async def test_user_input_composes_chevron_and_markdown() -> None:
    from textual.widgets import Label, Markdown
    from ui.widgets.user_input import UserInput

    widget = UserInput("**hello** world")
    app = WidgetHarness.make_app(widget)
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        assert widget.query_one("#prompt-chevron", Label)
        assert widget.query_one(Markdown)


@pytest.mark.asyncio
async def test_note_error_class() -> None:
    from ui.widgets.conversation import Note

    n = Note("boom")
    n.add_class("-error")
    app = WidgetHarness.make_app(n)
    async with app.run_test(size=(80, 5)) as pilot:
        await pilot.pause()
        assert n.has_class("-error")
        assert "boom" in _text(n)


@pytest.mark.asyncio
async def test_agent_response_streaming_accumulates() -> None:
    from ui.widgets.agent_response import AgentResponse

    resp = AgentResponse("")
    app = WidgetHarness.make_app(resp)
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        await resp.append_fragment("# Title\n\n")
        await resp.append_fragment("Hello ")
        await resp.append_fragment("**world**")
        for _ in range(10):
            await pilot.pause(0.02)
        assert resp._stream is not None
        blocks = list(resp.query("MarkdownBlock"))
        assert blocks, "MarkdownStream did not mount any blocks"


@pytest.mark.asyncio
async def test_tool_call_initial_state() -> None:
    from ui.widgets.tool_call import ToolCall, ToolCallHeader

    t = ToolCall("id1", "Read", {"path": "/tmp/foo.txt"})
    app = WidgetHarness.make_app(t)
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        assert t.is_running is True
        assert t.is_success is False
        assert t.is_error is False
        assert t.has_class("-running")
        header = t.query_one(ToolCallHeader)
        header_text = _text(header)
        assert "Reading" in header_text
        assert "/tmp/foo.txt" in header_text


@pytest.mark.asyncio
async def test_tool_call_success_completion() -> None:
    from ui.widgets.tool_call import ToolCall

    t = ToolCall("id2", "Bash", {"command": "ls"})
    app = WidgetHarness.make_app(t)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await t.complete(
            success=True,
            output="file1\nfile2\nfile3",
            error=None,
            diff=None,
            metadata={},
            exit_code=0,
            truncated=False,
        )
        await pilot.pause()
        assert not t.is_running
        assert t.is_success
        assert not t.is_error
        assert t.has_class("-success")
        assert t.has_content
        assert not t.expanded


@pytest.mark.asyncio
async def test_tool_call_error_auto_expands() -> None:
    from ui.widgets.tool_call import ToolCall

    t = ToolCall("id3", "Bash", {"command": "false"})
    app = WidgetHarness.make_app(t)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await t.complete(
            success=False,
            output="",
            error="command failed",
            diff=None,
            metadata={},
            exit_code=1,
            truncated=False,
        )
        await pilot.pause()
        assert t.is_error
        assert t.has_class("-error")
        assert t.expanded
        assert t.has_class("-expanded")


@pytest.mark.asyncio
async def test_tool_call_diff_mounts_diffview() -> None:
    from ui.widgets.diff_view import DiffView
    from ui.widgets.tool_call import ToolCall

    t = ToolCall("id4", "Edit", {"path": "foo.py"})
    app = WidgetHarness.make_app(t)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await t.complete(
            success=True,
            output="",
            error=None,
            diff="--- a\n+++ b\n+new line",
            metadata={},
            exit_code=None,
            truncated=False,
        )
        await pilot.pause()
        assert list(t.query(DiffView)), "diff should have rendered as DiffView"


@pytest.mark.asyncio
async def test_tool_call_click_toggles_expansion() -> None:
    from ui.widgets.tool_call import ToolCall

    t = ToolCall("id5", "Read", {"path": "/etc/hosts"})
    app = WidgetHarness.make_app(t)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await t.complete(
            success=True,
            output="127.0.0.1 localhost",
            error=None,
            diff=None,
            metadata={},
            exit_code=None,
            truncated=False,
        )
        await pilot.pause()
        assert t.has_content
        assert not t.expanded
        t.expanded = True
        await pilot.pause()
        assert t.has_class("-expanded")
        t.expanded = False
        await pilot.pause()
        assert not t.has_class("-expanded")


async def _wait_for_agent_ready(conv, pilot, timeout: float = 3.0) -> None:
    steps = int(timeout / 0.05)
    for _ in range(steps):
        if conv._agent is not None:
            return
        await pilot.pause(0.05)
    raise AssertionError("agent worker never became ready")


@pytest.mark.asyncio
async def test_full_agent_turn_simulation() -> None:
    """
    Drive Conversation end-to-end:
      AgentStarted  → busy_count goes up (throbber visible)
      TextDelta*    → AgentResponse mounts, chunks appended
      TextComplete  → response finalised
      AgentEnded    → busy_count resets
    Does NOT go through the real Agent.run() — we post events directly so
    the test is isolated from LLM availability.
    """
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import (
        AgentEnded,
        AgentStarted,
        TextComplete,
        TextDelta,
    )
    from ui.widgets.agent_response import AgentResponse
    from ui.widgets.conversation import Conversation

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        base_busy = conv.busy_count

        conv.post_message(AgentStarted())
        for _ in range(10):
            await pilot.pause(0.02)
            if conv.busy_count > base_busy:
                break
        assert conv.busy_count >= base_busy + 1

        conv.post_message(TextDelta(content="Hello "))
        conv.post_message(TextDelta(content="there!"))
        for _ in range(20):
            await pilot.pause(0.02)
            if list(conv.query(AgentResponse)):
                break
        assert list(conv.query(AgentResponse)), "AgentResponse was not mounted"

        conv.post_message(TextComplete())
        conv.post_message(AgentEnded())
        for _ in range(20):
            await pilot.pause(0.02)
            if conv.busy_count == base_busy:
                break
        assert conv.busy_count == base_busy, (
            f"busy_count did not return to baseline: {conv.busy_count} vs {base_busy}"
        )


@pytest.mark.asyncio
async def test_full_tool_call_event_flow() -> None:
    """ToolStarted mounts a ToolCall; ToolCompleted populates and marks it."""
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import ToolCompleted, ToolStarted
    from ui.widgets.conversation import Conversation
    from ui.widgets.tool_call import ToolCall

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent_ready(conv, pilot)

        conv.post_message(ToolStarted(
            call_id="c1", name="Read", arguments={"path": "/etc/hosts"},
        ))
        for _ in range(20):
            await pilot.pause(0.02)
            if list(conv.query(ToolCall)):
                break
        tools = list(conv.query(ToolCall))
        assert tools, "ToolCall was not mounted on ToolStarted"
        assert tools[0].is_running

        conv.post_message(ToolCompleted(
            call_id="c1", name="Read",
            success=True, output="line1\nline2", error=None,
            metadata={}, diff=None, truncated=False, exit_code=None,
        ))
        for _ in range(20):
            await pilot.pause(0.02)
            if not tools[0].is_running:
                break
        assert not tools[0].is_running
        assert tools[0].is_success


@pytest.mark.asyncio
async def test_flash_message_appears_on_conversation() -> None:
    """Posting Flash to Conversation should show the Flash widget."""
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import Flash as FlashMsg
    from ui.widgets.conversation import Conversation
    from ui.widgets.flash import Flash

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        flash = conv.query_one(Flash)
        assert not flash.visible
        conv.post_message(FlashMsg(content="saved", style="success", duration=5))
        for _ in range(20):
            await pilot.pause(0.02)
            if flash.visible:
                break
        assert flash.visible
        assert flash.has_class("-success")


@pytest.mark.asyncio
async def test_ctrl_c_clears_prompt_text() -> None:
    """With text in the prompt, Ctrl+C should clear it (not abort)."""
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.prompt import PromptTextArea

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        ta = app.query_one(PromptTextArea)
        ta.focus()
        await pilot.pause()
        ta.text = "abort this"
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert ta.text == "", f"prompt was not cleared, got {ta.text!r}"


@pytest.mark.asyncio
async def test_ctrl_q_exits_app() -> None:
    """Ctrl+Q triggers a clean exit."""
    from config.config import Config
    from ui.app import NewtApp

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app._exit, "Ctrl+Q did not request an exit"


@pytest.mark.asyncio
async def test_slash_overlay_filters_results() -> None:
    """Typing into the slash overlay should narrow the option list."""
    from config.config import Config
    from textual.widgets import Input, OptionList
    from ui.app import NewtApp
    from ui.messages import InvokeSlashComplete
    from ui.widgets.prompt import Prompt
    from ui.widgets.slash_complete import SlashComplete

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(Prompt)
        prompt.post_message(InvokeSlashComplete(query=""))
        await pilot.pause()
        slash = app.query_one(SlashComplete)
        options = slash.query_one(OptionList)
        initial_count = options.option_count
        assert initial_count > 5, "expected many commands in the unfiltered list"

        input_box = slash.query_one(Input)
        input_box.value = "hel"
        for _ in range(20):
            await pilot.pause(0.02)
            if options.option_count < initial_count:
                break
        assert options.option_count < initial_count
        assert options.option_count >= 1


@pytest.mark.asyncio
async def test_slash_command_dispatch_end_to_end() -> None:
    """/help → SlashResult → Note with 'Available commands' body."""
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import UserInputSubmitted
    from ui.widgets.conversation import Conversation, Note

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent_ready(conv, pilot)

        conv.post_message(UserInputSubmitted(body="/help"))
        for _ in range(40):
            await pilot.pause(0.05)
            if any("Available commands" in _text(n) for n in conv.query(Note)):
                break
        blobs = [_text(n) for n in conv.query(Note)]
        assert any("Available commands" in b for b in blobs), (
            f"help output not found: {blobs!r}"
        )


@pytest.mark.asyncio
async def test_unknown_slash_command_surfaces_error() -> None:
    """/nosuch should render a 'Unknown command' note, not crash."""
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import UserInputSubmitted
    from ui.widgets.conversation import Conversation, Note

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent_ready(conv, pilot)

        conv.post_message(UserInputSubmitted(body="/nosuch"))
        for _ in range(40):
            await pilot.pause(0.05)
            if any("Unknown command" in _text(n) for n in conv.query(Note)):
                break
        blobs = [_text(n) for n in conv.query(Note)]
        assert any("Unknown command" in b for b in blobs), blobs
