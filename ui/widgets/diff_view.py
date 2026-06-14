"""
Homegrown unified-diff viewer.

We don't depend on the `textual-diff-view` package toad uses — we build a
minimal but complete diff renderer on Textual primitives:

  • Parses unified diff text (the `git diff -u` format)
  • Renders with semantic colors (additions green, removals red, context dim)
  • Optional split (side-by-side) mode parses each hunk into left/right pairs
  • Line numbers in the gutter
  • Annotations (file path) at the top
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.reactive import reactive
from textual.widgets import Static


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER = re.compile(r"^(?:---|\+\+\+) ")


@dataclass(frozen=True)
class DiffLine:
    kind: Literal["context", "add", "remove", "header", "hunk"]
    text: str
    old_lineno: int | None
    new_lineno: int | None


def parse_unified_diff(diff_text: str) -> list[DiffLine]:
    """Parse a unified-diff string into a flat list of DiffLine records."""
    lines: list[DiffLine] = []
    old_lineno = new_lineno = 0

    for raw in diff_text.splitlines():
        if not raw:
            continue
        if raw.startswith("diff ") or _FILE_HEADER.match(raw):
            lines.append(DiffLine("header", raw, None, None))
            continue
        if (m := _HUNK_HEADER.match(raw)) is not None:
            old_lineno = int(m.group(1))
            new_lineno = int(m.group(3))
            lines.append(DiffLine("hunk", raw, None, None))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            lines.append(DiffLine("add", raw[1:], None, new_lineno))
            new_lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            lines.append(DiffLine("remove", raw[1:], old_lineno, None))
            old_lineno += 1
        else:
            text = raw[1:] if raw.startswith(" ") else raw
            lines.append(DiffLine("context", text, old_lineno, new_lineno))
            old_lineno += 1
            new_lineno += 1
    return lines


def _render_unified(diff: list[DiffLine]) -> Content:
    """Render diff lines as a single Content with semantic spans + line nos."""
    parts: list = []
    for line in diff:
        if line.kind == "header":
            parts.append((f"  {line.text}\n", "$text-muted dim"))
        elif line.kind == "hunk":
            parts.append((f"  {line.text}\n", "$text-accent"))
        elif line.kind == "add":
            ln = f"{line.new_lineno or '':>4} "
            parts.append((ln, "$text-success 60%"))
            parts.append(("+ ", "$text-success bold"))
            parts.append((line.text + "\n", "$text-success on $success 10%"))
        elif line.kind == "remove":
            ln = f"{line.old_lineno or '':>4} "
            parts.append((ln, "$text-error 60%"))
            parts.append(("- ", "$text-error bold"))
            parts.append((line.text + "\n", "$text-error on $error 10%"))
        else:
            ln = f"{line.new_lineno or '':>4} "
            parts.append((ln, "$text-muted"))
            parts.append(("  ",))
            parts.append((line.text + "\n", "$text-muted"))
    return Content.assemble(*[p for p in parts if isinstance(p, tuple) and len(p) >= 1])


def _split_hunks(
    diff: list[DiffLine],
) -> list[tuple[list[DiffLine], list[DiffLine]]]:
    """Group consecutive add/remove/context runs into (left, right) pairs."""
    pairs: list[tuple[list[DiffLine], list[DiffLine]]] = []
    left: list[DiffLine] = []
    right: list[DiffLine] = []

    def flush() -> None:
        nonlocal left, right
        if left or right:
            max_len = max(len(left), len(right))
            while len(left) < max_len:
                left.append(DiffLine("context", "", None, None))
            while len(right) < max_len:
                right.append(DiffLine("context", "", None, None))
            pairs.append((left, right))
            left, right = [], []

    for line in diff:
        if line.kind in ("header", "hunk"):
            flush()
            pairs.append(([line], [line]))
            continue
        if line.kind == "remove":
            left.append(line)
        elif line.kind == "add":
            right.append(line)
        else:
            flush()
            pairs.append(([line], [line]))
    flush()
    return pairs


def _render_split_side(side: list[DiffLine], *, side_kind: Literal["left", "right"]) -> Content:
    parts: list = []
    for line in side:
        if line.kind == "header":
            parts.append((f"  {line.text}\n", "$text-muted dim"))
        elif line.kind == "hunk":
            parts.append((f"  {line.text}\n", "$text-accent"))
        elif line.kind == "add":
            ln = f"{line.new_lineno or '':>4} "
            parts.append((ln, "$text-success 60%"))
            parts.append(("+ ", "$text-success bold"))
            parts.append((line.text + "\n", "$text-success on $success 10%"))
        elif line.kind == "remove":
            ln = f"{line.old_lineno or '':>4} "
            parts.append((ln, "$text-error 60%"))
            parts.append(("- ", "$text-error bold"))
            parts.append((line.text + "\n", "$text-error on $error 10%"))
        else:
            ln_val = (line.old_lineno if side_kind == "left" else line.new_lineno) or ""
            ln = f"{ln_val:>4} "
            parts.append((ln, "$text-muted"))
            parts.append(("  ",))
            parts.append((line.text + "\n", "$text-muted"))
    return Content.assemble(*[p for p in parts if isinstance(p, tuple) and len(p) >= 1])


class DiffView(Vertical):
    """Diff display with unified or split mode, optional file-name annotation."""

    DEFAULT_CSS = """
    DiffView {
        height: auto;
        background: $surface;
        border: round $primary 30%;
        padding: 0 1;

        #diff-title {
            color: $text-secondary;
            text-style: bold;
            padding: 0 0 1 0;
        }

        VerticalScroll {
            height: auto;
            max-height: 30;
            scrollbar-size-horizontal: 0;
        }

        Static {
            background: transparent;
        }

        &.-split {
            #diff-pane { layout: horizontal; }
            #diff-left, #diff-right {
                width: 1fr;
                height: auto;
            }
            #diff-left { border-right: solid $primary 30%; padding-right: 1; }
            #diff-right { padding-left: 1; }
        }
    }
    """

    split: reactive[bool] = reactive(False, toggle_class="-split")

    def __init__(
        self,
        diff_text: str,
        *,
        path: str | None = None,
        split: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._diff_text = diff_text
        self._path = path
        self._lines = parse_unified_diff(diff_text)
        self.set_reactive(DiffView.split, split)
        if split:
            self.add_class("-split")

    def compose(self) -> ComposeResult:
        if self._path:
            yield Static(
                Content.assemble((f"⎯ {self._path}", "$text-secondary bold")),
                id="diff-title",
            )
        with VerticalScroll(id="diff-scroll"):
            if self.split:
                with Horizontal(id="diff-pane"):
                    pairs = _split_hunks(self._lines)
                    left = [ln for left_side, _ in pairs for ln in left_side]
                    right = [ln for _, right_side in pairs for ln in right_side]
                    yield Static(_render_split_side(left, side_kind="left"), id="diff-left")
                    yield Static(_render_split_side(right, side_kind="right"), id="diff-right")
            else:
                yield Static(_render_unified(self._lines), id="diff-unified")

    @property
    def diff_text(self) -> str:
        return self._diff_text

    @property
    def lines(self) -> list[DiffLine]:
        return self._lines


def make_diff(
    diff_text: str,
    *,
    path: str | None = None,
    split: bool = False,
) -> DiffView:
    """Convenience constructor matching toad's make_diff() shape."""
    return DiffView(diff_text, path=path, split=split)
