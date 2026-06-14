"""
@-trigger fuzzy file picker overlay.

Simplified port of toad/widgets/path_search.py. We keep the fuzzy-list
path; the dual-mode tree picker is left as a follow-up. Hidden behind
a CSS class on the parent Prompt — same pattern as SlashComplete.
"""

from __future__ import annotations

import asyncio
from operator import itemgetter
from pathlib import Path
from typing import Sequence

from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.cache import LRUCache
from textual.containers import VerticalGroup
from textual.content import Content, Span
from textual.reactive import var
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from ui.fuzzy import FuzzySearch
from ui.fuzzy_index import FuzzyIndex
from ui.messages import Dismiss, InsertPath


_HIDDEN = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules",
    ".DS_Store", ".idea", ".vscode",
}


def _scan_paths(root: Path, *, max_paths: int = 5000) -> list[str]:
    """Walk `root`, collect relative paths, skipping hidden dirs."""
    out: list[str] = []
    root = root.resolve()

    def _walk(directory: Path) -> None:
        if len(out) >= max_paths:
            return
        try:
            entries = list(directory.iterdir())
        except (PermissionError, OSError):
            return
        entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        for entry in entries:
            if entry.name in _HIDDEN or entry.name.startswith("._"):
                continue
            if len(out) >= max_paths:
                return
            try:
                rel = entry.relative_to(root)
            except ValueError:
                continue
            if entry.is_dir():
                out.append(f"{rel}/")
                _walk(entry)
            else:
                out.append(str(rel))

    _walk(root)
    return out


class PathSearch(VerticalGroup):
    """Fuzzy file picker. Open via prompt's `@` key."""

    BINDINGS = [
        Binding("up", "cursor_up", "Cursor up", priority=True),
        Binding("down", "cursor_down", "Cursor down", priority=True),
        Binding("enter", "submit", "Insert path", priority=True, show=False),
        Binding("escape", "dismiss", "Dismiss", priority=True, show=False),
    ]

    DEFAULT_CSS = """
    PathSearch {
        display: none;
        overlay: screen;
        offset-y: -16;
        height: 16;
        width: 1fr;
        background: $surface;
        border: round $primary 60%;

        Input {
            margin: 0 1;
            padding: 0 1;
            background: transparent;
            border: none;
            height: 3;
        }
        OptionList {
            border: none;
            height: 1fr;
            padding: 0;
            margin: 0 1;
            text-wrap: nowrap;
            text-overflow: ellipsis;
            background: transparent;
            & > .option-list--option { padding: 0 1; }
            & > .option-list--option-highlighted { background: $primary 30%; }
        }
    }
    """

    root: var[Path] = var(Path("./"))
    paths: var[list[str]] = var(list)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.set_reactive(PathSearch.root, root.resolve())
        self._fuzzy = FuzzySearch(case_sensitive=False)
        self._index = FuzzyIndex()
        self._cache: LRUCache = LRUCache(1024)

    def compose(self) -> ComposeResult:
        yield Input(placeholder="@-fuzzy file search · esc to cancel", id="path-input")
        yield OptionList(id="path-options")

    def on_mount(self) -> None:
        self._refresh_paths()


    def open(self) -> None:
        self._toggle_parent("-show-path-search", True)
        try:
            inp = self.query_one("#path-input", Input)
            inp.value = ""
            self.call_after_refresh(inp.focus)
        except Exception:
            pass

    def close(self) -> None:
        self._toggle_parent("-show-path-search", False)

    def _toggle_parent(self, cls: str, on: bool) -> None:
        node = self.parent
        while node is not None and node.__class__.__name__ != "Prompt":
            node = node.parent
        if node is not None:
            node.set_class(on, cls)


    @work(exclusive=True, group="path-scan")
    async def _refresh_paths(self) -> None:
        try:
            paths = await asyncio.to_thread(_scan_paths, self.root)
        except Exception:
            paths = []
        self.paths = paths
        await self._index.update_paths(paths)
        self._render_options(paths[:100])

    def _render_options(self, paths: Sequence[str], offsets_by_path: dict[str, Sequence[int]] | None = None) -> None:
        options: list[Option] = []
        for path in paths:
            content = Content(path)
            if offsets_by_path is not None and (off := offsets_by_path.get(path)):
                content = content.add_spans(
                    [Span(o, o + 1, "underline $text-primary") for o in off]
                )
            options.append(Option(content, id=path))
        try:
            lst = self.query_one("#path-options", OptionList)
        except Exception:
            return
        lst.clear_options()
        if options:
            lst.add_options(options)
            lst.highlighted = 0


    @on(Input.Changed, "#path-input")
    async def _on_input_changed(self, event: Input.Changed) -> None:
        await self._search(event.value)

    async def _search(self, query: str) -> None:
        if not query:
            self._render_options(self.paths[:100])
            return
        candidates = await self._index.search(query)
        scored: list[tuple[float, Sequence[int], str]] = []
        for path in candidates[:300]:
            score, offsets = self._fuzzy.match(query, path)
            if score > 0:
                scored.append((score, offsets, path))
        scored.sort(key=itemgetter(0), reverse=True)
        top = scored[:30]
        offsets_by_path = {p: o for _, o, p in top}
        self._render_options([p for _, _, p in top], offsets_by_path)


    def action_cursor_up(self) -> None:
        try:
            self.query_one("#path-options", OptionList).action_cursor_up()
        except Exception:
            pass

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#path-options", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_dismiss(self) -> None:
        self.close()
        self.post_message(Dismiss())

    def action_submit(self) -> None:
        try:
            lst = self.query_one("#path-options", OptionList)
        except Exception:
            return
        if lst.highlighted is None:
            return
        opt = lst.get_option_at_index(lst.highlighted)
        if opt.id:
            self.post_message(InsertPath(path=opt.id))
            self.close()
            self.post_message(Dismiss())

    @on(OptionList.OptionSelected, "#path-options")
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.post_message(InsertPath(path=event.option.id))
            self.close()
            self.post_message(Dismiss())

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.action_dismiss()
