"""
Transient toast banner at the bottom of the conversation.

Pattern (from toad/widgets/flash.py): a Static widget whose visibility and
semantic style class are flipped on demand, auto-hiding via `set_timer`.
No animation — the lifecycle IS the UX.
"""

from __future__ import annotations

from typing import Literal

from textual.content import Content
from textual.timer import Timer
from textual.widgets import Static


Style = Literal["default", "success", "warning", "error"]


class Flash(Static):
    DEFAULT_CSS = """
    Flash {
        dock: bottom;
        width: 100%;
        height: 1;
        padding: 0 2;
        text-align: center;
        visibility: hidden;
        background: $primary 15%;
        color: $text-primary;

        &.-default { background: $primary 15%; color: $text-primary; }
        &.-success { background: $success 15%; color: $text-success; }
        &.-warning { background: $warning 15%; color: $text-warning; }
        &.-error   { background: $error   15%; color: $text-error;   }
    }
    """

    _STYLE_CLASSES = ("-default", "-success", "-warning", "-error")

    def __init__(self) -> None:
        super().__init__("")
        self._timer: Timer | None = None

    def flash(
        self,
        content: str | Content,
        *,
        style: Style = "default",
        duration: float = 3.0,
    ) -> None:
        """Show `content` with the given semantic style, auto-hide after `duration`."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        self.update(content)
        self.remove_class(*self._STYLE_CLASSES, update=False)
        self.add_class(f"-{style}")
        self.visible = True

        self._timer = self.set_timer(duration, self._hide)

    def _hide(self) -> None:
        self.visible = False
        self._timer = None
