"""
Slash-command completion overlay.

Pattern (from toad/widgets/slash_complete.py):
  - A VerticalGroup containing an Input and an OptionList.
  - Lives inside the prompt; hidden by default; revealed via a CSS class
    toggle on the parent Prompt widget.
  - `overlay: screen` lifts it above sibling content; `offset-y: -{h}`
    positions it just above the prompt bar.
  - Fuzzy-filter by prefix match on the command name. Exact-prefix hits
    rank first. Selection emits SlashCommandChosen and dismisses.
"""

from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.containers import VerticalGroup
from textual.content import Content
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from ui.messages import Dismiss, SlashCommandChosen


class SlashComplete(VerticalGroup):
    DEFAULT_CSS = """
    SlashComplete {
        display: none;
        overlay: screen;
        offset-y: -14;
        height: 14;
        width: 1fr;
        background: $surface;
        border: round $primary 60%;

        Input {
            margin: 0 1;
            padding: 0 1;
            background: transparent;
            border: none;
            height: 3;
        }
        OptionList {
            border: none;
            height: 1fr;
            padding: 0;
            margin: 0 1;
            text-wrap: nowrap;
            text-overflow: ellipsis;
            background: transparent;

            & > .option-list--option { padding: 0 1; }
            & > .option-list--option-highlighted { background: $primary 30%; }
        }
    }
    """

    def __init__(self, commands: dict[str, str]) -> None:
        """commands = {"help": "Show available commands", ...}"""
        super().__init__()
        self._commands = commands
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="slash command…", id="slash-input")
        yield OptionList(id="slash-options")

    def on_mount(self) -> None:
        self._refresh_options("")


    def open(self, prefill: str = "") -> None:
        """Reveal the overlay; focus the input; optionally prefill the filter."""
        inp = self.query_one("#slash-input", Input)
        inp.value = prefill
        self._refresh_options(prefill)
        self._set_visible(True)
        self.call_after_refresh(inp.focus)

    def close(self) -> None:
        self._set_visible(False)

    def _set_visible(self, visible: bool) -> None:
        self.parent_prompt_set_class("-show-slash-complete", visible)

    def parent_prompt_set_class(self, cls: str, on: bool) -> None:
        node = self.parent
        while node is not None and not node.__class__.__name__ == "Prompt":
            node = node.parent
        if node is not None:
            node.set_class(on, cls)


    def _refresh_options(self, query: str) -> None:
        q = query.lstrip("/").lower()
        options: list[Option] = []

        def score(name: str) -> tuple[int, str]:
            name_l = name.lower()
            if q == "":
                return (0, name_l)
            if name_l.startswith(q):
                return (-2, name_l)
            if q in name_l:
                return (-1, name_l)
            return (1, name_l)

        filtered = [
            (name, desc)
            for name, desc in self._commands.items()
            if q == "" or q in name.lower()
        ]
        filtered.sort(key=lambda kv: score(kv[0]))

        for name, desc in filtered[:50]:
            label = Content.assemble(
                (f"/{name}", "$text-primary bold"),
                ("  "),
                (desc or "", "$text-muted"),
            )
            options.append(Option(label, id=name))

        lst = self.query_one("#slash-options", OptionList)
        lst.clear_options()
        lst.add_options(options)
        if options:
            lst.highlighted = 0


    @on(Input.Changed, "#slash-input")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        self._refresh_options(event.value)

    @on(Input.Submitted, "#slash-input")
    def _on_filter_submit(self) -> None:
        self._choose_highlighted()

    @on(OptionList.OptionSelected, "#slash-options")
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose_option(event.option)

    def _choose_highlighted(self) -> None:
        lst = self.query_one("#slash-options", OptionList)
        if lst.highlighted is None:
            self.close()
            self.post_message(Dismiss())
            return
        self._choose_option(lst.get_option_at_index(lst.highlighted))

    def _choose_option(self, option: Option) -> None:
        self.close()
        if option.id:
            self.post_message(SlashCommandChosen(command=f"/{option.id}"))
        self.post_message(Dismiss())

    def on_key(self, event: events.Key) -> None:
        if event.key in ("down", "up", "pagedown", "pageup"):
            lst = self.query_one("#slash-options", OptionList)
            if event.key == "down":
                lst.action_cursor_down()
            elif event.key == "up":
                lst.action_cursor_up()
            elif event.key == "pagedown":
                lst.action_page_down()
            elif event.key == "pageup":
                lst.action_page_up()
            event.stop()
        elif event.key == "escape":
            self.close()
            self.post_message(Dismiss())
            event.stop()
