"""
Bottom input bar.

Structure (simplified port of toad/widgets/prompt.py):
  ┌ prompt-container ────────────────────────────────────┐
  │  ❯  [ PromptTextArea (markdown / shell highlighted) ] │
  └──────────────────────────────────────────────────────┘
  [ AgentInfo ] [ CondensedPath ]            [ StatusLine ]

  + SlashComplete overlay (hidden unless -show-slash-complete)

Submit on Enter posts UserInputSubmitted. Ctrl+J for newline. "/" at
empty prompt opens the slash-complete overlay.
"""

from __future__ import annotations

from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, VerticalGroup
from textual.reactive import var
from textual.widgets import Label

from ui.messages import (
    Dismiss,
    InsertPath,
    InvokePathSearch,
    InvokeSlashComplete,
    SlashCommandChosen,
    UserInputSubmitted,
)
from ui.widgets.agent_info import AgentInfo
from ui.widgets.condensed_path import CondensedPath
from ui.widgets.highlighted_textarea import HighlightedTextArea
from ui.widgets.path_search import PathSearch
from ui.widgets.slash_complete import SlashComplete
from ui.widgets.status_line import StatusLine


class PromptTextArea(HighlightedTextArea):
    """Chat input — Enter submits, Ctrl+J inserts newline, `/` opens overlay."""

    BINDINGS = [
        Binding("enter", "submit", "Send", show=True),
        Binding("ctrl+j", "newline", "Newline", show=True),
        Binding("shift+enter", "newline", "Newline", show=False),
    ]

    def action_submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        self.text = ""
        self.post_message(UserInputSubmitted(body=text))

    def action_newline(self) -> None:
        self.insert("\n")

    def on_key(self, event: events.Key) -> None:
        if event.character == "/" and self.text == "":
            event.stop()
            event.prevent_default()
            self.post_message(InvokeSlashComplete(query=""))
        elif event.character == "@":
            self.post_message(InvokePathSearch(query=""))


class Prompt(VerticalGroup):
    DEFAULT_CSS = """
    Prompt {
        dock: bottom;
        height: auto;
        layer: prompt;

        #prompt-container {
            &:dark { background: black 10%; }
            &:light { background: white 40%; }
            height: auto;
            margin: 0 0 1 0;
            border: tall transparent;
            &:focus-within { border: tall $secondary; }

            #chevron {
                padding: 0 1;
                width: 3;
                color: $text-muted;
            }
            PromptTextArea {
                padding: 0 1 0 0;
                background: transparent;
                border: none;
                height: auto;
                max-height: 50vh;
                &:blur { text-opacity: 0.5; }
            }
        }

        #info-container {
            height: 1;
            margin: 0 1;
            color: $text-muted;

            AgentInfo {
                margin: 0 1 0 0;
            }
            CondensedPath {
                width: 1fr;
                margin: 0 1;
                color: $text-secondary;
            }
            StatusLine {
                width: auto;
                text-align: right;
            }
        }
    }
    """

    cwd: var[Path] = var(Path.cwd())
    model_name: var[str] = var("")
    status: var[str] = var("")

    def __init__(self, commands: dict[str, str]) -> None:
        super().__init__()
        self._commands = commands

    def compose(self) -> ComposeResult:
        yield SlashComplete(self._commands)
        yield PathSearch(self.cwd)
        with HorizontalGroup(id="prompt-container"):
            yield Label("❯", id="chevron")
            yield PromptTextArea()
        with HorizontalGroup(id="info-container"):
            yield AgentInfo("", id="agent-info")
            yield CondensedPath(id="cwd-path")
            yield StatusLine(id="status-line")

    def on_mount(self) -> None:
        self._refresh_info()

    def watch_cwd(self, cwd: Path) -> None:
        self._refresh_info()

    def watch_model_name(self, model: str) -> None:
        self._refresh_info()

    def watch_status(self, status: str) -> None:
        try:
            self.query_one(StatusLine).status = status
        except Exception:
            pass

    def _refresh_info(self) -> None:
        try:
            self.query_one(AgentInfo).model_name = self.model_name
            self.query_one(CondensedPath).path = str(self.cwd)
        except Exception:
            pass

    @on(InvokeSlashComplete)
    def _on_invoke_slash(self, event: InvokeSlashComplete) -> None:
        event.stop()
        self.query_one(SlashComplete).open(event.query)

    @on(InvokePathSearch)
    def _on_invoke_path(self, event: InvokePathSearch) -> None:
        event.stop()
        self.query_one(PathSearch).open()

    @on(InsertPath)
    def _on_insert_path(self, event: InsertPath) -> None:
        """User picked a path — paste it into the prompt at the cursor."""
        event.stop()
        try:
            ta = self.query_one(PromptTextArea)
        except Exception:
            return
        text = ta.text
        if text.endswith("@"):
            ta.text = text[:-1] + event.path
        else:
            ta.insert(event.path)
        ta.focus()

    @on(Dismiss)
    def _on_dismiss(self, event: Dismiss) -> None:
        event.stop()
        self.query_one(PromptTextArea).focus()

    @on(SlashCommandChosen)
    def _on_slash_chosen(self, event: SlashCommandChosen) -> None:
        self.query_one(PromptTextArea).focus()
