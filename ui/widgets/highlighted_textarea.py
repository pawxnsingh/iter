"""
TextArea with live Pygments syntax highlighting.

Port of toad/widgets/highlighted_textarea.py:
  - `TextualHighlightTheme` maps Pygments tokens → Textual style strings
    (CSS variables like `$text-accent`), so theme switching re-colors
    everything without changes here.
  - `highlight_language` reactive toggles between markdown (for normal
    prompts) and shell (for `!cmd` mode).
  - The widget caches per-line highlighted Content and invalidates on
    text change or style update.
  - `highlight_regex(RE_MATCH_FILE_PROMPT)` paints `@file` references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from pygments.token import Token
from rich.text import Text
from textual import on
from textual.content import Content
from textual.highlight import HighlightTheme, TokenType, highlight
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import TextArea
from textual.widgets.text_area import Selection


# `@path/to/file` or `@"path with spaces"`
RE_MATCH_FILE_PROMPT = re.compile(r"(@\S+)|@\"(.*)\"")


class TextualHighlightTheme(HighlightTheme):
    """Pygments token → Textual style string mapping."""

    STYLES: dict[TokenType, str] = {
        Token.Comment: "$text 60%",
        Token.Error: "$text-error on $error-muted",
        Token.Generic.Strong: "bold",
        Token.Generic.Emph: "italic",
        Token.Generic.Error: "$text-error on $error-muted",
        Token.Generic.Heading: "$text-primary underline",
        Token.Generic.Subheading: "$text-primary",
        Token.Keyword: "$text-accent",
        Token.Keyword.Constant: "bold $text-success 80%",
        Token.Keyword.Namespace: "$text-error",
        Token.Keyword.Type: "bold",
        Token.Literal.Number: "$text-warning",
        Token.Literal.String.Backtick: "$text 60%",
        Token.Literal.String: "$text-success 90%",
        Token.Literal.String.Doc: "$text-success 80% italic",
        Token.Literal.String.Double: "$text-success 90%",
        Token.Name: "$text-primary",
        Token.Name.Attribute: "$text-warning",
        Token.Name.Builtin: "$text-accent",
        Token.Name.Builtin.Pseudo: "italic",
        Token.Name.Class: "$text-warning bold",
        Token.Name.Constant: "$text-error",
        Token.Name.Decorator: "$text-primary bold",
        Token.Name.Entity: "$text",
        Token.Name.Function: "$text-warning underline",
        Token.Name.Function.Magic: "$text-warning underline",
        Token.Name.Tag: "$text-primary bold",
        Token.Name.Variable: "$text-secondary",
        Token.Number: "$text-warning",
        Token.Operator: "bold",
        Token.Operator.Word: "bold $text-error",
        Token.String: "$text-success",
        Token.Whitespace: "",
    }


class HighlightedTextArea(TextArea):
    """TextArea that highlights its own content via Pygments on each change."""

    highlight_language: reactive[str] = reactive("markdown")

    @dataclass
    class CursorMove(Message):
        selection: Selection

    def __init__(
        self,
        text: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        placeholder: str | Content = "",
    ) -> None:
        self._text_cache: dict[int, Text] = {}
        self._highlight_lines: list[Content] | None = None
        super().__init__(
            text,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            highlight_cursor_line=False,
            placeholder=placeholder,
        )
        self.compact = True

    # ─── Cache lifecycle ────────────────────────────────────────────

    def _clear_caches(self) -> None:
        self._highlight_lines = None
        self._text_cache.clear()

    def notify_style_update(self) -> None:
        self._clear_caches()
        return super().notify_style_update()

    def _watch_selection(
        self, previous_selection: Selection, selection: Selection
    ) -> None:
        self.post_message(self.CursorMove(selection))
        super()._watch_selection(previous_selection, selection)

    @on(TextArea.Changed)
    def _on_changed(self) -> None:
        self._clear_caches()

    # ─── Highlighting ───────────────────────────────────────────────

    @property
    def highlight_lines(self) -> Sequence[Content]:
        if self._highlight_lines is None:
            text = self.text
            if text.startswith("/") and "\n" not in text:
                self._highlight_lines = [self.highlight_slash_command(text)]
                return self._highlight_lines

            lang = self.highlight_language
            if lang == "markdown":
                content = self.highlight_markdown(text)
                # highlight() adds a trailing ``` to keep the fence parser
                # happy; split & drop that virtual last line.
                self._highlight_lines = content.split("\n", allow_blank=True)[:-1]
            elif lang == "shell":
                content = self.highlight_shell(text)
                self._highlight_lines = content.split("\n", allow_blank=True)
            else:
                raise ValueError(f"unsupported highlight_language: {lang!r}")
        return self._highlight_lines

    def highlight_slash_command(self, text: str) -> Content:
        return Content.styled(text, "$text-success")

    def highlight_markdown(self, text: str) -> Content:
        content = highlight(
            text + "\n```",
            language="markdown",
            theme=TextualHighlightTheme,
        )
        return content.highlight_regex(RE_MATCH_FILE_PROMPT, style="$primary")

    def highlight_shell(self, text: str) -> Content:
        return highlight(text, language="sh")

    def get_line(self, line_index: int) -> Text:
        if (cached := self._text_cache.get(line_index)) is not None:
            return cached.copy()
        try:
            line = self.highlight_lines[line_index]
        except IndexError:
            return Text("", end="", no_wrap=True)
        rendered = list(line.render_segments(self.visual_style))
        text = Text.assemble(
            *[(t, s) for t, s, _ in rendered], end="", no_wrap=True
        )
        self._text_cache[line_index] = text.copy()
        return text
