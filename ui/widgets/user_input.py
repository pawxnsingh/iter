"""
User message bubble — the chevron + Markdown-rendered prompt text.

Styled with a left border + tinted background to visually differentiate
from agent responses. Mirrors toad/widgets/user_input.py.
"""

from __future__ import annotations

from textual import containers
from textual.app import ComposeResult
from textual.widgets import Label, Markdown


class UserInput(containers.HorizontalGroup):
    DEFAULT_CSS = """
    UserInput {
        border-left: blank $secondary;
        background: $secondary 15%;
        padding: 1 1 1 0;
        margin: 1 1 1 0;
        height: auto;

        #prompt-chevron {
            margin: 0 1 0 1;
            color: $text-secondary;
            width: 1;
        }
        Markdown {
            padding: 0 2 0 0;
            width: 1fr;
        }
        Markdown > MarkdownBlock:last-child {
            margin-bottom: 0;
        }
    }
    """

    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content

    def compose(self) -> ComposeResult:
        yield Label("❯", id="prompt-chevron")
        yield Markdown(self.content, id="content")
