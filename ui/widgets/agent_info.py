"""
Small pill showing the active model name in the prompt info bar.
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Label


class AgentInfo(Label):
    DEFAULT_CSS = """
    AgentInfo {
        padding: 0 1;
        margin: 0 1 0 0;
        color: $text-primary;
        background: $primary 10%;
        width: auto;
    }
    """

    model_name: reactive[str] = reactive("")

    def watch_model_name(self, model: str) -> None:
        self.update(model or "?")
