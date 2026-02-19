"""
Block-cursor widgets — the ▌ left-margin indicator that follows block
navigation (Alt+Up/Down).

Port of toad's Cursor + CursorContainer (originally inside
widgets/conversation.py).
"""

from __future__ import annotations

from rich.segment import Segment
from textual import containers
from textual.geometry import Region
from textual.reactive import var
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static


class Cursor(Static):
    """A 1-cell wide block cursor that blinks while active."""

    DEFAULT_CSS = """
    Cursor {
        width: 1;
        height: 0;
        border-left: outer $text-accent;
        visibility: visible;
        &.-blink { border-left: outer $text-accent 20%; }
    }
    """

    follow_widget: var[Widget | None] = var(None)
    blink: var[bool] = var(True, toggle_class="-blink")

    def __init__(self) -> None:
        super().__init__("")
        self._blink_timer: Timer | None = None

    def on_mount(self) -> None:
        # Blink at 2 Hz; paused until we're actively following a block
        self._blink_timer = self.set_interval(0.5, self._update_blink, pause=True)

    def _update_blink(self) -> None:
        if self.screen.is_active:
            self.blink = not self.blink
        else:
            self.blink = False

    def attach_to(self, widget: Widget | None) -> None:
        """Position the cursor to cover `widget`'s region; pause blinking if None."""
        self.follow_widget = widget
        if widget is None:
            self.styles.height = 0
            if self._blink_timer is not None:
                self._blink_timer.pause()
            return
        try:
            self.styles.height = max(1, widget.size.height)
        except Exception:
            self.styles.height = 1
        if self._blink_timer is not None:
            self._blink_timer.resume()


class CursorContainer(containers.Vertical):
    """A 1-column container that paints a faint ▌ rail down its height.

    Used as the left column of a 2-column grid; the Cursor widget sits on
    top of the rail and visually "rides" the rail as blocks scroll.
    """

    DEFAULT_CSS = """
    CursorContainer {
        width: 1;
        color: $foreground 7%;
        height: auto;
    }
    """

    def render_lines(self, crop: Region) -> list[Strip]:
        rich_style = self.visual_style.rich_style
        strips = [Strip([Segment("▌", rich_style)], cell_length=1)] * crop.height
        # Leave the very first line blank so the rail doesn't clash with the
        # conversation's top border / throbber
        if crop.y == 0 and strips:
            strips[0] = Strip([Segment(" ", rich_style)], cell_length=1)
        return strips
