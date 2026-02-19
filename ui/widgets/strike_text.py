"""
Progressive strike-through animation widget.

Port of toad/widgets/strike_text.py — identical technique:
  - `strike()` records the start time and enables auto_refresh at 30 FPS.
  - `render()` computes the strikethrough range from `monotonic()` delta,
    applies it via `Content.stylize("strike ...", 0, position)`, and
    disables auto_refresh when the animation finishes.
"""

from __future__ import annotations

from time import monotonic

from textual.content import Content
from textual.reactive import reactive
from textual.widget import Widget


class StrikeText(Widget):
    DEFAULT_CSS = """
    StrikeText {
        height: auto;
        &.-complete {
            text-style: strike;
            color: $text-primary;
        }
    }
    """

    strike_time: reactive[float | None] = reactive(None)

    # Characters per second of strike advance
    _RATE = 70.0

    def __init__(
        self,
        content: Content,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        self.content = content
        super().__init__(name=name, id=id, classes=classes)

    def strike(self) -> None:
        """Kick off the animation."""
        self.strike_time = monotonic()
        self.auto_refresh = 1 / 30

    def render(self) -> Content:
        content = self.content
        if self.strike_time is not None:
            position = int((monotonic() - self.strike_time) * self._RATE)
            content = content.stylize("strike $text-primary", 0, position)
            if position > len(content):
                self.auto_refresh = None
        return content
