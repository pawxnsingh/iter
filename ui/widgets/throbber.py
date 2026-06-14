"""
Gradient-animated loading indicator.

Inspired by toad/widgets/throbber.py. The original uses Textual's Visual
protocol for zero-allocation per-frame rendering; we use the simpler
`render()` path because it's compatible across more Textual versions and
15 FPS is fine for a 1-line widget.

The visual: a rainbow gradient of "━" characters that scrolls left→right,
visible only when the `-busy` CSS class is present.
"""

from __future__ import annotations

from time import monotonic

from rich.color import Color as RichColor
from rich.style import Style as RichStyle
from rich.text import Text
from textual.widget import Widget


_COLORS = [
    "#BD93F9", "#D6ACFF", "#FF79C6", "#FF92DF", "#FF5555",
    "#FFB86C", "#F1FA8C", "#50FA7B", "#69FF94", "#8BE9FD",
    "#A4FFFF", "#BD93F9",
]


def _blend(a: RichColor, b: RichColor, t: float) -> RichColor:
    """Linear RGB blend between two Rich Colors."""
    ar, ag, ab = a.triplet
    br, bg, bb = b.triplet
    return RichColor.from_rgb(
        ar + (br - ar) * t,
        ag + (bg - ag) * t,
        ab + (bb - ab) * t,
    )


def _gradient_color(progress: float) -> RichColor:
    """Pick a color at `progress` in [0, 1) along the gradient."""
    progress = progress % 1.0
    scaled = progress * (len(_COLORS) - 1)
    idx = int(scaled)
    frac = scaled - idx
    return _blend(
        RichColor.parse(_COLORS[idx]),
        RichColor.parse(_COLORS[idx + 1]),
        frac,
    )


class Throbber(Widget):
    """One-line gradient scroller, toggled via the `-busy` class."""

    DEFAULT_CSS = """
    Throbber {
        width: 100%;
        height: 1;
        visibility: hidden;
        &.-busy { visibility: visible; }
    }
    """

    CHARACTER = "━"
    AUTO_REFRESH_INTERVAL = 1 / 15

    def on_mount(self) -> None:
        self.auto_refresh = self.AUTO_REFRESH_INTERVAL

    def render(self) -> Text:
        width = max(1, self.size.width)
        t = monotonic()
        phase = (t % 1.0)
        text = Text(no_wrap=True, overflow="crop")
        for x in range(width):
            col = _gradient_color((x / width + phase) % 1.0)
            text.append(self.CHARACTER, RichStyle(color=col))
        return text

    def set_busy(self, busy: bool) -> None:
        self.set_class(busy, "-busy")
