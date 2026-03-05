"""
Right-aligned single-line status widget.

Shows token usage / cost / context-percent — anything that changes
turn-by-turn. Port of the `StatusLine` pattern used in toad's info bar.
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Label


class StatusLine(Label):
    DEFAULT_CSS = """
    StatusLine {
        color: $secondary 70%;
        margin: 0 1;
        width: auto;
        height: 1;
        text-align: right;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    status: reactive[str] = reactive("")

    def watch_status(self, status: str) -> None:
        self.update(status)

    def set_token_stats(self, stats: dict | None) -> None:
        """Format a ContextManager.get_token_stats() dict for display.

        Expected keys: current_tokens, context_window, percent_used.
        """
        if not stats:
            self.status = ""
            return
        current = stats.get("current_tokens", 0)
        window = stats.get("context_window", 0)
        pct = stats.get("percent_used", 0)
        self.status = f"{current:,} / {window:,} ({pct}%)"
