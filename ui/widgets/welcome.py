"""
Welcome / logo screen shown at the top of an empty conversation.

Port of toad/widgets/welcome.py with our own ASCII art and intro.
"""

from __future__ import annotations

from textual import containers
from textual.app import ComposeResult
from textual.widgets import Label, Markdown


ASCII_LOGO = r"""
    ╭─────────────────────╮
    │   ◆◆◆  PieAgent     │
    │ ◆    ◆              │
    │◆      ◆  coding CLI │
    │ ◆    ◆              │
    │   ◆◆◆               │
    ╰─────────────────────╯
"""


WELCOME_MD = """\
## Welcome

Type a message below to start coding.
Use `/help` for available commands · `@` to reference a file · `F1` for shortcuts.
"""


class Welcome(containers.Vertical):
    DEFAULT_CSS = """
    Welcome {
        height: auto;
        padding: 1 2;
        #logo {
            color: $text-success;
            padding: 0 1;
        }
        #message {
            background: transparent;
            padding: 0;
            width: 1fr;
            height: auto;
        }
    }
    """

    def compose(self) -> ComposeResult:
        with containers.Center():
            yield Label(ASCII_LOGO, id="logo")
        yield Markdown(WELCOME_MD, id="message", classes="note")
