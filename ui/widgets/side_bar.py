"""
Collapsible sidebar for panels (Plan, Project tree, etc.).

Port of toad/widgets/side_bar.py — SideBar.Panel dataclass lets callers
declaratively pass (title, widget, flex, collapsed, id) tuples; SideBar
wraps each in a Collapsible with the right `-flex` or `-fixed` class so
CSS can distribute space.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import containers, widgets
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget


class SideBarCollapsible(widgets.Collapsible):
    BINDING_GROUP_TITLE = "Sidebar collapsible"


class SideBar(containers.Vertical):
    BINDING_GROUP_TITLE = "Sidebar"
    BINDINGS = [("escape", "dismiss", "Dismiss sidebar")]

    class Dismiss(Message):
        """Posted when the sidebar wants to yield focus back to the prompt."""

    @dataclass(frozen=True)
    class Panel:
        title: str
        widget: Widget
        flex: bool = False
        collapsed: bool = False
        id: str | None = None

    def __init__(
        self,
        *panels: Panel,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.panels: list[SideBar.Panel] = list(panels)

    def on_mount(self) -> None:
        try:
            self.trap_focus()
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        for panel in self.panels:
            yield SideBarCollapsible(
                panel.widget,
                title=panel.title,
                collapsed=panel.collapsed,
                classes="-flex" if panel.flex else "-fixed",
                id=panel.id,
            )

    def action_dismiss(self) -> None:
        self.post_message(self.Dismiss())
