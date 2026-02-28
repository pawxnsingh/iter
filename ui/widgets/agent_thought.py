"""
Agent's thinking/reasoning bubble — separate from the final response.

Port of toad/widgets/agent_thought.py. Collapsible when short
(max-height 10 with `-loading`); becomes maximizable when focused.
"""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.reactive import var
from textual.widgets import Markdown

try:
    from textual.widgets._markdown import MarkdownStream
except ImportError:  # pragma: no cover
    MarkdownStream = None  # type: ignore[assignment]


class AgentThought(Markdown, can_focus=True):
    """The agent's running reasoning stream."""

    DEFAULT_CLASSES = "block"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "scroll_up", "Scroll up", show=False),
        Binding("down", "scroll_down", "Scroll down", show=False),
        Binding("left", "scroll_left", "Scroll left", show=False),
        Binding("right", "scroll_right", "Scroll right", show=False),
        Binding("home", "scroll_home", "Scroll home", show=False),
        Binding("end", "scroll_end", "Scroll end", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
    ]

    ALLOW_MAXIMIZE = True
    _stream: var = var(None)

    def watch_loading(self, loading: bool) -> None:
        self.set_class(loading, "-loading")

    def on_mount(self) -> None:
        self.scroll_end()

    @property
    def stream(self):
        if self._stream is None:
            self._stream = self.get_stream(self)
        return self._stream

    async def append_fragment(self, fragment: str) -> None:
        if not fragment:
            return
        self.loading = False
        await self.stream.write(fragment)
        self.scroll_end()
