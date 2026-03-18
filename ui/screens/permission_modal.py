"""
Permission approval modal.

Called from the Agent's permission_prompt_fn when a tool wants authorization.
Returns True/False via push_screen_wait.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, HorizontalGroup, VerticalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class PermissionModal(ModalScreen[bool]):
    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }
    #container {
        width: 60%;
        max-width: 90;
        height: auto;
        padding: 1 2;
        border: thick $warning 60%;
        background: $surface;
    }
    #title { padding: 0 0 1 0; text-style: bold; color: $text-warning; }
    #body { padding: 0 0 1 0; color: $text-muted; }
    #buttons { width: 100%; align-horizontal: right; padding: 1 0 0 0; }
    #buttons Button { margin: 0 0 0 2; }
    .-destructive #title { color: $text-error; }
    .-destructive { border: thick $error 70%; }
    """

    BINDINGS = [
        Binding("escape", "deny", "Deny"),
        Binding("y", "allow", "Allow"),
        Binding("n", "deny", "Deny"),
    ]

    def __init__(self, tool_name: str, message: str, is_destructive: bool) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._message = message
        self._is_destructive = is_destructive

    def compose(self) -> ComposeResult:
        with Center():
            with VerticalGroup(
                id="container",
                classes="-destructive" if self._is_destructive else "",
            ):
                yield Label(
                    f"Permission required: {self._tool_name}", id="title"
                )
                yield Static(self._message, id="body")
                with HorizontalGroup(id="buttons"):
                    yield Button("Deny (n)", id="deny")
                    yield Button(
                        "Allow (y)",
                        variant="warning" if self._is_destructive else "primary",
                        id="allow",
                    )

    @on(Button.Pressed, "#allow")
    def _on_allow(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#deny")
    def _on_deny(self) -> None:
        self.dismiss(False)

    def action_allow(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
