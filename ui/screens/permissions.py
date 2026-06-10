"""
Full-screen permission approval — replaces the basic Y/N modal.

Shows the tool name, description, and (for Edit/Write tools) a DiffView of
the proposed change. Returns one of: "allow_once", "allow_always",
"reject_once", "reject_always".
"""

from __future__ import annotations

from typing import Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from ui.widgets.diff_view import DiffView


Decision = Literal["allow_once", "allow_always", "reject_once", "reject_always"]


class PermissionsScreen(Screen[Decision]):
    """Returns one of the four Decision strings, never None."""

    DEFAULT_CSS = """
    PermissionsScreen {
        background: $background;
        align: center top;
    }
    .top {
        height: 1fr;
        background: $background;
        padding: 1 2;
        layout: vertical;
    }
    #header {
        height: auto;
        padding: 1 0 1 0;
    }
    #title {
        color: $text-warning;
        text-style: bold;
        padding: 0 0 1 0;
    }
    .-destructive #title { color: $text-error; }
    #description {
        padding: 0 0 1 0;
        color: $text-muted;
    }
    #diff-container {
        height: 1fr;
        background: $surface;
        border: round $primary 30%;
        padding: 1;
        margin: 0 0 1 0;
    }
    #buttons {
        height: 3;
        padding: 0;
        align-horizontal: right;
    }
    #buttons Button {
        margin-left: 1;
        min-width: 16;
    }
    """

    BINDINGS = [
        Binding("a", "allow_once", "Allow once", priority=True),
        Binding("A", "allow_always", "Allow always", priority=True),
        Binding("r", "reject_once", "Reject", priority=True),
        Binding("R", "reject_always", "Reject always", priority=True),
        Binding("escape", "reject_once", "Cancel", show=False),
    ]

    def __init__(
        self,
        tool_name: str,
        message: str,
        *,
        is_destructive: bool = False,
        diff_text: str | None = None,
        diff_path: str | None = None,
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._message = message
        self._is_destructive = is_destructive
        self._diff_text = diff_text
        self._diff_path = diff_path
        if is_destructive:
            self.add_class("-destructive")

    def compose(self) -> ComposeResult:
        with Container(classes="top"):
            with Vertical(id="header"):
                yield Static(
                    Content.assemble(
                        ("Permission required: ", "$text-muted"),
                        (self._tool_name, "$text-warning bold"),
                    ),
                    id="title",
                )
                yield Static(self._message, id="description")

            if self._diff_text:
                with Container(id="diff-container"):
                    yield DiffView(self._diff_text, path=self._diff_path)

            with Horizontal(id="buttons"):
                yield Button("Reject (r)", id="reject-once", variant="error")
                yield Button("Reject always (R)", id="reject-always")
                yield Button("Allow (a)", id="allow-once", variant="primary")
                yield Button("Allow always (A)", id="allow-always", variant="success")
        yield Footer()

    @on(Button.Pressed, "#allow-once")
    def _allow_once(self) -> None:
        self.dismiss("allow_once")

    @on(Button.Pressed, "#allow-always")
    def _allow_always(self) -> None:
        self.dismiss("allow_always")

    @on(Button.Pressed, "#reject-once")
    def _reject_once(self) -> None:
        self.dismiss("reject_once")

    @on(Button.Pressed, "#reject-always")
    def _reject_always(self) -> None:
        self.dismiss("reject_always")

    def action_allow_once(self) -> None:
        self.dismiss("allow_once")

    def action_allow_always(self) -> None:
        self.dismiss("allow_always")

    def action_reject_once(self) -> None:
        self.dismiss("reject_once")

    def action_reject_always(self) -> None:
        self.dismiss("reject_always")
