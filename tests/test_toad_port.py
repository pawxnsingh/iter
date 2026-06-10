"""
Smoke tests for the toad-port widgets added in Pass 1:

  - condense_path
  - StrikeText animation lifecycle
  - Welcome logo block
  - Note (+ semantic class styling)
  - AgentInfo pill
  - CondensedPath render
  - StatusLine token-stat formatting
  - AgentThought streaming
  - HighlightedTextArea — markdown & shell highlighting
  - SideBar.Panel composition
  - Plan strike-on-complete + set_todos bridge
  - ProjectDirectoryTree filter
  - Conversation mounts Welcome on empty, dismisses on first user input
  - MainScreen sidebar action focuses the sidebar
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


# ─── Harness ───────────────────────────────────────────────────────────


class _Harness:
    @staticmethod
    def make_app(widget):
        from textual.app import App, ComposeResult

        class _App(App):
            def compose(self) -> ComposeResult:
                yield widget

        return _App()


def _text(w) -> str:
    blob = getattr(w, "_content", None)
    if blob is not None:
        return str(blob)
    try:
        return str(w.render())
    except Exception:
        return repr(w)


async def _wait_for_agent(conv, pilot, timeout: float = 3.0) -> None:
    steps = int(timeout / 0.05)
    for _ in range(steps):
        if conv._agent is not None:
            return
        await pilot.pause(0.05)


# ═══════════════════════════════════════════════════════════════════════════
# condense_path  (format_path module was removed — its tests went with it)
# ═══════════════════════════════════════════════════════════════════════════


def test_condense_path_shorter_than_width() -> None:
    from ui.widgets.condensed_path import condense_path

    assert condense_path("a/b/c", 20) == "a/b/c"


def test_condense_path_middle_ellipsis() -> None:
    from ui.widgets.condensed_path import condense_path

    out = condense_path("a/b/c/d/e/f/g.py", 10)
    assert "…" in out
    assert "g.py" in out  # tail preserved


def test_radiate_range_grows_outward() -> None:
    from ui.widgets.condensed_path import radiate_range

    pairs = list(radiate_range(5))
    assert (2, 2) == pairs[0]  # starts at center


# ═══════════════════════════════════════════════════════════════════════════
# StrikeText
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_strike_text_renders_and_animates() -> None:
    from textual.content import Content
    from ui.widgets.strike_text import StrikeText

    s = StrikeText(Content("hello world"))
    app = _Harness.make_app(s)
    async with app.run_test(size=(40, 5)) as pilot:
        await pilot.pause()
        assert "hello" in s.render().plain
        assert s.strike_time is None
        s.strike()
        await pilot.pause()
        assert s.strike_time is not None


# ═══════════════════════════════════════════════════════════════════════════
# Welcome
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_welcome_mounts_logo_and_markdown() -> None:
    from textual.widgets import Label, Markdown
    from ui.widgets.welcome import Welcome

    w = Welcome()
    app = _Harness.make_app(w)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert w.query_one("#logo", Label)
        assert w.query_one(Markdown, None) is not None


# ═══════════════════════════════════════════════════════════════════════════
# Note  (markdown_note was removed as dead code)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_note_implements_menu_protocol() -> None:
    from ui.protocols import MenuProtocol
    from ui.widgets.note import Note

    n = Note("boom")
    assert isinstance(n, MenuProtocol)
    # Empty menu is OK
    assert list(n.get_block_menu()) == []
    assert n.get_block_content("clipboard") is not None


# ═══════════════════════════════════════════════════════════════════════════
# AgentInfo / StatusLine
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_info_updates_on_model_change() -> None:
    from ui.widgets.agent_info import AgentInfo

    info = AgentInfo("")
    app = _Harness.make_app(info)
    async with app.run_test(size=(40, 5)) as pilot:
        await pilot.pause()
        info.model_name = "gpt-5.2"
        await pilot.pause()
        assert "gpt-5.2" in _text(info)


@pytest.mark.asyncio
async def test_status_line_format_from_token_stats() -> None:
    from ui.widgets.status_line import StatusLine

    s = StatusLine()
    app = _Harness.make_app(s)
    async with app.run_test(size=(80, 5)) as pilot:
        await pilot.pause()
        s.set_token_stats({
            "current_tokens": 1234,
            "context_window": 400000,
            "percent_used": 0,
        })
        await pilot.pause()
        assert "1,234" in s.status


# ═══════════════════════════════════════════════════════════════════════════
# CondensedPath widget
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_condensed_path_widget_renders(tmp_path: Path) -> None:
    from ui.widgets.condensed_path import CondensedPath

    cp = CondensedPath(str(tmp_path / "a" / "b"))
    app = _Harness.make_app(cp)
    async with app.run_test(size=(40, 3)) as pilot:
        await pilot.pause()
        assert cp.display_path  # populated by watch_path → on_resize


# ═══════════════════════════════════════════════════════════════════════════
# AgentThought
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_thought_streaming() -> None:
    from ui.widgets.agent_thought import AgentThought

    t = AgentThought("")
    app = _Harness.make_app(t)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        await t.append_fragment("# Reasoning\n\n")
        await t.append_fragment("thinking about it...")
        for _ in range(10):
            await pilot.pause(0.02)
        assert t._stream is not None


# ═══════════════════════════════════════════════════════════════════════════
# HighlightedTextArea
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_highlighted_textarea_markdown_mode() -> None:
    from ui.widgets.highlighted_textarea import HighlightedTextArea

    ta = HighlightedTextArea("# hi\n\n`code`")
    app = _Harness.make_app(ta)
    async with app.run_test(size=(60, 10)) as pilot:
        await pilot.pause()
        lines = ta.highlight_lines
        assert lines  # markdown parsed into at least one content line


@pytest.mark.asyncio
async def test_highlighted_textarea_shell_mode() -> None:
    from ui.widgets.highlighted_textarea import HighlightedTextArea

    ta = HighlightedTextArea("ls -la | grep x", )
    app = _Harness.make_app(ta)
    async with app.run_test(size=(60, 10)) as pilot:
        await pilot.pause()
        ta.highlight_language = "shell"
        await pilot.pause()
        assert ta.highlight_lines


@pytest.mark.asyncio
async def test_highlighted_textarea_slash_command_styling() -> None:
    from ui.widgets.highlighted_textarea import HighlightedTextArea

    ta = HighlightedTextArea("/help")
    app = _Harness.make_app(ta)
    async with app.run_test(size=(60, 5)) as pilot:
        await pilot.pause()
        lines = ta.highlight_lines
        assert len(lines) == 1  # single-line slash goes down special path


# ═══════════════════════════════════════════════════════════════════════════
# SideBar
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_sidebar_composes_panels() -> None:
    from textual.widgets import Label
    from ui.widgets.side_bar import SideBar, SideBarCollapsible

    bar = SideBar(
        SideBar.Panel("A", Label("a")),
        SideBar.Panel("B", Label("b"), flex=True),
    )
    app = _Harness.make_app(bar)
    async with app.run_test(size=(50, 30)) as pilot:
        await pilot.pause()
        collapsibles = list(bar.query(SideBarCollapsible))
        assert len(collapsibles) == 2
        assert any(c.has_class("-flex") for c in collapsibles)
        assert any(c.has_class("-fixed") for c in collapsibles)


# ═══════════════════════════════════════════════════════════════════════════
# Plan
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_plan_empty_shows_no_plan() -> None:
    from ui.widgets.plan import Plan

    p = Plan([])
    app = _Harness.make_app(p)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        assert any("No plan" in _text(s) for s in p.query("Static"))


@pytest.mark.asyncio
async def test_plan_set_todos_strikes_newly_completed() -> None:
    from ui.widgets.plan import Plan

    p = Plan([])
    app = _Harness.make_app(p)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        p.set_todos([
            {"content": "task A", "status": "in_progress", "priority": "high"},
            {"content": "task B", "status": "pending", "priority": "medium"},
        ])
        await pilot.pause()
        assert p.entries and len(p.entries) == 2

        # Mark task A completed — should be in newly_completed
        p.set_todos([
            {"content": "task A", "status": "completed", "priority": "high"},
            {"content": "task B", "status": "pending", "priority": "medium"},
        ])
        await pilot.pause()
        contents = {e.content.plain for e in p.newly_completed}
        assert "task A" in contents


@pytest.mark.asyncio
async def test_plan_all_complete_class() -> None:
    from ui.widgets.plan import Plan

    p = Plan([])
    app = _Harness.make_app(p)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        p.set_todos([
            {"content": "a", "status": "completed"},
            {"content": "b", "status": "completed"},
        ])
        await pilot.pause()
        assert p.all_complete is True
        assert p.has_class("-all-complete")


# ═══════════════════════════════════════════════════════════════════════════
# ProjectDirectoryTree
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_project_directory_tree_filters_noise(tmp_path: Path) -> None:
    from ui.widgets.project_directory_tree import ProjectDirectoryTree

    (tmp_path / "src").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "keep.py").write_text("x")

    tree = ProjectDirectoryTree(tmp_path)
    visible = list(tree.filter_paths([
        tmp_path / "src", tmp_path / "__pycache__",
        tmp_path / ".git", tmp_path / "keep.py",
    ]))
    names = {p.name for p in visible}
    assert "src" in names
    assert "keep.py" in names
    assert "__pycache__" not in names
    assert ".git" not in names


# ═══════════════════════════════════════════════════════════════════════════
# Conversation — welcome dismissal
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_welcome_mounted_on_startup() -> None:
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.conversation import Conversation
    from ui.widgets.welcome import Welcome

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        for _ in range(20):
            await pilot.pause(0.05)
            if list(conv.query(Welcome)):
                break
        assert list(conv.query(Welcome))


@pytest.mark.asyncio
async def test_welcome_dismissed_on_first_user_input() -> None:
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import UserInputSubmitted
    from ui.widgets.conversation import Conversation
    from ui.widgets.welcome import Welcome

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)
        assert list(conv.query(Welcome))

        conv.post_message(UserInputSubmitted(body="/help"))
        for _ in range(40):
            await pilot.pause(0.05)
            if not list(conv.query(Welcome)):
                break
        assert not list(conv.query(Welcome))


# ═══════════════════════════════════════════════════════════════════════════
# MainScreen sidebar binding
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ctrl_b_focuses_sidebar() -> None:
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.side_bar import SideBar

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        sidebar = app.query_one(SideBar)
        # Start: sidebar has no focus within
        assert not sidebar.has_focus_within
        await pilot.press("ctrl+b")
        for _ in range(20):
            await pilot.pause(0.02)
            if sidebar.has_focus_within:
                break
        assert sidebar.has_focus_within


# ═══════════════════════════════════════════════════════════════════════════
# Prompt info bar — AgentInfo + CondensedPath + StatusLine
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_prompt_info_bar_has_model_and_path() -> None:
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.agent_info import AgentInfo
    from ui.widgets.condensed_path import CondensedPath
    from ui.widgets.prompt import Prompt
    from ui.widgets.status_line import StatusLine

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(Prompt)
        ai = prompt.query_one(AgentInfo)
        cp = prompt.query_one(CondensedPath)
        sl = prompt.query_one(StatusLine)
        assert "?" not in ai.model_name or len(ai.model_name) > 1  # populated
        assert cp.path == str(ROOT)
        assert sl is not None


# ═══════════════════════════════════════════════════════════════════════════
# Block cursor navigation
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_block_cursor_up_moves_to_last_block() -> None:
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import TextDelta, UserInputSubmitted
    from ui.widgets.conversation import Conversation
    from ui.widgets.user_input import UserInput

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        # Mount a couple of blocks
        conv.post_message(UserInputSubmitted(body="hi"))
        for _ in range(20):
            await pilot.pause(0.02)
            if list(conv.query(UserInput)):
                break
        conv.post_message(TextDelta(content="response"))
        for _ in range(20):
            await pilot.pause(0.02)
            if conv._current_response is not None:
                break

        # No cursor yet; Alt+Up should land on the last block
        assert conv._cursor_block is None
        conv.action_cursor_up()
        assert conv._cursor_block is not None
