"""
PieAgentApp — Textual app entry point.

Mirrors toad/app.py at a much smaller scale:
  - Single MainScreen (no MODES yet — multi-session can be added later).
  - Dracula-ish ANSI theme so embedded shell output looks right.
  - Settings, sessions, etc. handled by the existing slash-command layer
    (no new settings UI yet).
"""

from __future__ import annotations

from typing import ClassVar

from rich import terminal_theme
from textual.app import App
from textual.binding import Binding, BindingType
from textual.screen import Screen

from config.config import Config
from ui.screens.main import MainScreen


# Same Dracula palette toad uses for ANSI rendering inside terminal widgets.
DRACULA_TERMINAL_THEME = terminal_theme.TerminalTheme(
    background=(40, 42, 54),
    foreground=(248, 248, 242),
    normal=[
        (33, 34, 44), (255, 85, 85), (80, 250, 123), (241, 250, 140),
        (189, 147, 249), (255, 121, 198), (139, 233, 253), (248, 248, 242),
    ],
    bright=[
        (98, 114, 164), (255, 110, 110), (105, 255, 148), (255, 255, 165),
        (214, 172, 255), (255, 146, 223), (164, 255, 255), (255, 255, 255),
    ],
)


class PieAgentApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "PieAgent"
    SUB_TITLE = ""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "Quit", priority=True, show=False),
        Binding("f1", "toggle_help_panel", "Help", priority=True, show=False),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def get_default_screen(self) -> Screen:
        """Return MainScreen as the initial screen — no push needed."""
        return MainScreen(self.config)

    def on_mount(self) -> None:
        try:
            self.theme = "dracula"
        except Exception:
            pass
        self.ansi_theme_dark = DRACULA_TERMINAL_THEME


def run_tui(config: Config) -> int:
    """Blocking entry point — used by main.py when --tui is passed."""
    app = PieAgentApp(config)
    app.run()
    return 0
