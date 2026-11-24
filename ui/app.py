"""
IterApp — Textual app entry point.
"""

from __future__ import annotations
from typing import ClassVar, TYPE_CHECKING

from rich import terminal_theme

from textual.app import App
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.reactive import reactive, var

from config import settings
from config.config import Config
from utils.quote import QUOTES

from ui.screens.main import MainScreen


DARK_TERMINAL_THEME = terminal_theme.TerminalTheme(
    background=(24, 24, 24),  # #181818
    foreground=(214, 214, 221),  # #D6D6DD
    normal=[
        (103, 103, 103),   # black -   #676767
        (241, 76, 76),     # red -     #F14C4C
        (21, 172, 145),    # green -   #15AC91
        (229, 185, 92),    # yellow -  #E5B95C
        (76, 157, 243),    # blue -    #4C9DF3
        (229, 103, 220),   # magenta - #E567DC
        (117, 211, 186),   # cyan -    #75D3BA
        (214, 214, 221),   # white -   #D6D6DD
    ],
    bright=[
        (128, 128, 128),   # black -   #808080
        (244, 112, 112),   # red -     #F47070
        (68, 189, 167),    # green -   #44BDA7
        (234, 199, 125),   # yellow -  #EAC77D
        (112, 177, 245),   # blue -    #70B1F5
        (234, 133, 227),   # magenta - #EA85E3
        (145, 220, 200),   # cyan -    #91DCC8
        (255, 255, 255),   # white -   #FFFFFF
    ],
)

class IterApp(App, inherit_bindings=False):
    """The top level app."""
        
    # class level configurations
    CSS_PATH = "app.tcss"    
    SCREENS = {
        "settings": "",
        "sessions": ""
    }
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (100, "-wide")]  # responsive CSS classes
    ALLOW_IN_MAXIMIZED_VIEW = ""    
    PAUSE_GC_ON_SCROLL = True                 # perf tuning
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "ctrl+q",
            "quit",
            "Quit",
            tooltip="Quit the app and return to the command prompt.",
            show=False,
            priority=True,
        ),
        Binding("ctrl+c", "help_quit", show=False, system=True),
        Binding("ctrl+s", "sessions", "Sessions"),
        Binding("f1", "toggle_help_panel", "Help", priority=True),
        Binding(
            "f2,ctrl+comma",
            "settings",
            "Settings",
            tooltip="Settings screen",
        ),
    ]
    
    #reactive state
    column: reactive[bool] = reactive(False)
    column_width: reactive[int] = reactive(100)
    terminal_title: var[str] = var("Iter")
    show_sessions = var(False, toggle_class="-show-sessions-bar")
    
    
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def get_default_screen(self) -> Screen:
        """Return MainScreen as the initial screen — no push needed."""
        return MainScreen(self.config)

    def on_mount(self) -> None:
        try:
            self.theme = "textual_dark"
        except Exception:
            pass
        self.ansi_theme_dark = DARK_TERMINAL_THEME
    