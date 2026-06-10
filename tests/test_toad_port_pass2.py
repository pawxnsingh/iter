"""
Smoke tests for the Pass-2 toad ports:
  - DiffView (parse + unified + split rendering)
  - Columns visual (auto + flex distribution, content rendering)
  - FuzzySearch / FuzzyIndex
  - PathSearch overlay open/close + filter
  - CommandEditModal (returns text or None)
  - SessionResumeModal (DataTable picker)
  - PermissionsScreen (4-way Decision)
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


class _Harness:
    @staticmethod
    def make_app(widget):
        from textual.app import App, ComposeResult

        class _App(App):
            def compose(self) -> ComposeResult:
                yield widget

        return _App()


# ═══════════════════════════════════════════════════════════════════════════
# DiffView
# ═══════════════════════════════════════════════════════════════════════════


def test_parse_unified_diff_basic() -> None:
    from ui.widgets.diff_view import parse_unified_diff

    diff = """\
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 line1
-old
+new
 line3
"""
    lines = parse_unified_diff(diff)
    kinds = [line.kind for line in lines]
    assert "header" in kinds
    assert "hunk" in kinds
    assert kinds.count("add") == 1
    assert kinds.count("remove") == 1
    assert kinds.count("context") == 2


def test_parse_unified_diff_empty() -> None:
    from ui.widgets.diff_view import parse_unified_diff

    assert parse_unified_diff("") == []


def test_parse_unified_diff_lineno_tracking() -> None:
    from ui.widgets.diff_view import parse_unified_diff

    diff = """\
@@ -10,2 +20,2 @@
 ctx
-bad
+good
"""
    lines = parse_unified_diff(diff)
    add = next(line for line in lines if line.kind == "add")
    rem = next(line for line in lines if line.kind == "remove")
    assert add.new_lineno == 21
    assert rem.old_lineno == 11


@pytest.mark.asyncio
async def test_diff_view_renders_unified() -> None:
    from ui.widgets.diff_view import DiffView

    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"
    dv = DiffView(diff, path="foo.py")
    app = _Harness.make_app(dv)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert dv.query_one("#diff-unified")


@pytest.mark.asyncio
async def test_diff_view_split_mode_mounts_two_panes() -> None:
    from ui.widgets.diff_view import DiffView

    diff = "--- a\n+++ b\n@@ -1,2 +1,2 @@\n-old1\n-old2\n+new1\n+new2"
    dv = DiffView(diff, path="foo.py", split=True)
    app = _Harness.make_app(dv)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert dv.query_one("#diff-left")
        assert dv.query_one("#diff-right")
        assert dv.has_class("-split")


# ═══════════════════════════════════════════════════════════════════════════
# FuzzySearch
# ═══════════════════════════════════════════════════════════════════════════


def test_fuzzy_search_exact_match() -> None:
    from ui.fuzzy import FuzzySearch

    f = FuzzySearch()
    score, offsets = f.match("foo", "foo")
    assert score > 0
    assert list(offsets) == [0, 1, 2]


def test_fuzzy_search_subsequence() -> None:
    from ui.fuzzy import FuzzySearch

    f = FuzzySearch()
    score, _ = f.match("hl", "hello")
    assert score > 0


def test_fuzzy_search_no_match() -> None:
    from ui.fuzzy import FuzzySearch

    f = FuzzySearch()
    score, offsets = f.match("xyz", "abc")
    assert score == 0.0
    assert list(offsets) == []


def test_fuzzy_search_case_insensitive_default() -> None:
    from ui.fuzzy import FuzzySearch

    f = FuzzySearch()
    score, _ = f.match("FOO", "foobar")
    assert score > 0


# ═══════════════════════════════════════════════════════════════════════════
# FuzzyIndex
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fuzzy_index_search_finds_matches() -> None:
    from ui.fuzzy_index import FuzzyIndex

    idx = FuzzyIndex()
    await idx.update_paths([
        "src/foo.py",
        "src/bar.py",
        "tests/test_foo.py",
        "README.md",
    ])
    results = await idx.search("foo")
    assert "src/foo.py" in results
    assert "tests/test_foo.py" in results


@pytest.mark.asyncio
async def test_fuzzy_index_short_query() -> None:
    from ui.fuzzy_index import FuzzyIndex

    idx = FuzzyIndex()
    await idx.update_paths(["a.py", "ab.py", "abc.py", "xyz.py"])
    results = await idx.search("a")
    # Single-char query returns paths starting with the char
    assert any(r.startswith("a") for r in results)


# ═══════════════════════════════════════════════════════════════════════════
# PathSearch overlay (lightweight — no full integration)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_path_search_open_filter_render(tmp_path: Path) -> None:
    """Mount PathSearch standalone; populate paths; verify option list."""
    from textual.widgets import OptionList
    from ui.widgets.path_search import PathSearch

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("x")

    ps = PathSearch(tmp_path)
    app = _Harness.make_app(ps)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Wait for the path-scan worker to populate
        for _ in range(40):
            await pilot.pause(0.05)
            if ps.paths:
                break
        assert ps.paths

        # The OptionList should have at least the scanned files
        olist = ps.query_one(OptionList)
        assert olist.option_count >= 1


# CommandEditModal tests removed alongside the screen file (dead code).
# Restore both when the bash-tool-edit safety flow is actually wired.


# ═══════════════════════════════════════════════════════════════════════════
# SessionResumeModal
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_session_resume_modal_no_sessions() -> None:
    from textual import work
    from textual.app import App, ComposeResult
    from textual.widgets import Static
    from ui.screens.session_resume_modal import SessionResumeModal

    result: dict = {}

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield Static("host")

        def on_mount(self) -> None:
            self._show()

        @work
        async def _show(self) -> None:
            result["value"] = await self.push_screen_wait(SessionResumeModal([]))
            self.exit()

    app = _Host()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause(0.02)
            if isinstance(app.screen, SessionResumeModal):
                break
        # Modal mounts with "No previous sessions" notice. Static's text
        # lives in `.renderable` on this Textual version; fall back to
        # str(rendered) if a future version moves it.
        def _text(w) -> str:
            for attr in ("renderable", "_content"):
                v = getattr(w, attr, None)
                if v is not None:
                    return str(v)
            try:
                return str(w.render())
            except Exception:
                return ""
        assert any(
            "No previous" in _text(s) for s in app.screen.query("Static")
        )
        await pilot.press("escape")
        for _ in range(40):
            await pilot.pause(0.02)
            if "value" in result:
                break
        assert result.get("value") is None


def test_session_resume_friendly_time_ago() -> None:
    from datetime import datetime, timedelta, timezone
    from ui.screens.session_resume_modal import friendly_time_ago

    now = datetime.now(timezone.utc)
    assert friendly_time_ago(now.isoformat()) == "just now"
    assert "minute" in friendly_time_ago((now - timedelta(minutes=5)).isoformat())
    assert "hour" in friendly_time_ago((now - timedelta(hours=3)).isoformat())


# ═══════════════════════════════════════════════════════════════════════════
# PermissionsScreen
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_permissions_screen_allow_always() -> None:
    from textual import work
    from textual.app import App, ComposeResult
    from textual.widgets import Static
    from ui.screens.permissions import PermissionsScreen

    result: dict = {}

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield Static("host")

        def on_mount(self) -> None:
            self._show()

        @work
        async def _show(self) -> None:
            result["value"] = await self.push_screen_wait(
                PermissionsScreen("Bash", "rm -rf /tmp/x", is_destructive=True)
            )
            self.exit()

    app = _Host()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause(0.02)
            if isinstance(app.screen, PermissionsScreen):
                break
        await pilot.press("A")  # capital = allow_always
        for _ in range(40):
            await pilot.pause(0.02)
            if "value" in result:
                break
        assert result.get("value") == "allow_always"


@pytest.mark.asyncio
async def test_permissions_screen_with_diff() -> None:
    from textual import work
    from textual.app import App, ComposeResult
    from textual.widgets import Static
    from ui.screens.permissions import PermissionsScreen
    from ui.widgets.diff_view import DiffView

    result: dict = {}
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield Static("host")

        def on_mount(self) -> None:
            self._show()

        @work
        async def _show(self) -> None:
            result["value"] = await self.push_screen_wait(
                PermissionsScreen(
                    "Edit", "Modify foo.py",
                    diff_text=diff, diff_path="foo.py",
                )
            )
            self.exit()

    app = _Host()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.pause(0.02)
            if isinstance(app.screen, PermissionsScreen):
                break
        # Diff is mounted
        assert list(app.screen.query(DiffView))
        await pilot.press("r")
        for _ in range(40):
            await pilot.pause(0.02)
            if "value" in result:
                break
        assert result.get("value") == "reject_once"


# ═══════════════════════════════════════════════════════════════════════════
# Prompt @ key opens path search
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_at_key_opens_path_search() -> None:
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.prompt import Prompt, PromptTextArea

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        ta = app.query_one(PromptTextArea)
        ta.focus()
        await pilot.pause()
        assert not app.query_one(Prompt).has_class("-show-path-search")
        await pilot.press("@")
        for _ in range(20):
            await pilot.pause(0.02)
            if app.query_one(Prompt).has_class("-show-path-search"):
                break
        assert app.query_one(Prompt).has_class("-show-path-search")


@pytest.mark.asyncio
async def test_insert_path_message_replaces_at_in_prompt() -> None:
    from config.config import Config
    from ui.app import NewtApp
    from ui.messages import InsertPath
    from ui.widgets.prompt import Prompt, PromptTextArea

    app = NewtApp(Config(cwd=ROOT))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        ta = app.query_one(PromptTextArea)
        ta.text = "look at @"
        await pilot.pause()
        prompt = app.query_one(Prompt)
        prompt.post_message(InsertPath(path="src/foo.py"))
        for _ in range(20):
            await pilot.pause(0.02)
            if "src/foo.py" in ta.text:
                break
        # The trailing '@' is replaced by the path
        assert ta.text == "look at src/foo.py"
