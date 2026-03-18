"""
Main screen — composes Conversation + Prompt + Footer, with an optional
slide-out Sidebar showing the plan (TodoWrite) and project tree.

Keyboard bindings:
  Ctrl+C  abort turn (or clear prompt if there's text)
  Ctrl+Q  quit
  F1      toggle help panel
  Ctrl+B  focus sidebar
  Esc     dismiss sidebar → back to prompt
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer

from commands import get_all_commands
from config.config import Config
from ui.messages import RequestExit, SlashCommandChosen, UserInputSubmitted
from ui.widgets.conversation import Conversation
from ui.widgets.plan import Plan
from ui.widgets.project_directory_tree import ProjectDirectoryTree
from ui.widgets.prompt import Prompt, PromptTextArea
from ui.widgets.side_bar import SideBar


class MainScreen(Screen):
    AUTO_FOCUS = "PromptTextArea"
    CSS_PATH = "main.tcss"

    BINDINGS = [
        Binding("ctrl+c", "abort", "Cancel turn", show=True),
        Binding("ctrl+q", "quit_app", "Quit", show=True, priority=True),
        Binding("f1", "toggle_help", "Help", show=True, priority=True),
        Binding("ctrl+b", "show_sidebar", "Sidebar", show=True),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        # Sidebar first — docked left, hidden by default via CSS
        yield SideBar(
            SideBar.Panel("Plan", Plan([])),
            SideBar.Panel(
                "Project",
                ProjectDirectoryTree(self.config.cwd, id="project-dir-tree"),
                flex=True,
            ),
            id="sidebar",
        )

        yield Conversation(self.config)

        commands = {name: cmd.description for name, cmd in get_all_commands().items()}
        prompt = Prompt(commands)
        prompt.cwd = self.config.cwd
        prompt.model_name = self.config.model_name
        yield prompt

        yield Footer()

    # ─── Forward prompt input to the Conversation ─────────────────────

    @on(UserInputSubmitted)
    def _forward_input(self, event: UserInputSubmitted) -> None:
        event.stop()
        conv = self.query_one(Conversation)
        conv.post_message(UserInputSubmitted(body=event.body, shell=event.shell))

    @on(SlashCommandChosen)
    def _forward_slash(self, event: SlashCommandChosen) -> None:
        event.stop()
        conv = self.query_one(Conversation)
        conv.post_message(SlashCommandChosen(command=event.command))

    @on(RequestExit)
    def _on_exit_request(self, event: RequestExit) -> None:
        event.stop()
        self.app.exit(0)

    @on(SideBar.Dismiss)
    def _on_sidebar_dismiss(self, event: SideBar.Dismiss) -> None:
        event.stop()
        try:
            self.query_one(PromptTextArea).focus()
        except Exception:
            pass

    # ─── Actions ──────────────────────────────────────────────────────

    def action_abort(self) -> None:
        try:
            ta = self.query_one(PromptTextArea)
            if ta.has_focus and ta.text:
                ta.text = ""
                return
        except Exception:
            pass
        self.query_one(Conversation).abort_current()

    def action_quit_app(self) -> None:
        self.app.exit(0)

    def action_toggle_help(self) -> None:
        if self.query("HelpPanel"):
            self.app.action_hide_help_panel()
        else:
            self.app.action_show_help_panel()

    def action_show_sidebar(self) -> None:
        try:
            sidebar = self.query_one(SideBar)
        except Exception:
            return
        # Try to focus the first collapsible title so `:focus-within` styles fire
        titles = list(sidebar.query("Collapsible CollapsibleTitle"))
        if titles:
            titles[0].focus()
        else:
            sidebar.focus()
