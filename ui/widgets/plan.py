"""
Task-list widget with strike-through animation on completion.

Port of toad/widgets/plan.py. Wires to our TodoWrite tool output via
`set_todos(list[dict])`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual import containers
from textual.app import ComposeResult
from textual.content import Content
from textual.reactive import reactive
from textual.widgets import Static

from ui.widgets.pill import pill
from ui.widgets.strike_text import StrikeText


class NonSelectableStatic(Static):
    ALLOW_SELECT = False


class Plan(containers.Grid):
    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    Plan {
        border-top: ascii $secondary;
        border-bottom: ascii $secondary;
        background: black 10%;
        margin: 1 0 1 0;
        padding: 0 1 0 1;
        height: auto;
        grid-size: 2;
        grid-columns: auto 1fr;
        grid-rows: auto;
        border-title-align: center;

        .-no-plan { text-style: dim italic; padding: 0 1; }
        .plan   { color: $text-secondary; }
        .status { padding: 0; color: $text-secondary; }
        .status.status-completed { color: $text-success; }
        .status-pending { opacity: 0.7; }
    }
    """

    @dataclass(frozen=True)
    class Entry:
        content: Content
        priority: str = "medium"
        status: str = "pending"

        def with_status(self, status: str) -> "Plan.Entry":
            return Plan.Entry(self.content, self.priority, status)

    entries: reactive[list[Entry] | None] = reactive(None, recompose=True)
    all_complete: reactive[bool] = reactive(False, toggle_class="-all-complete")

    PRIORITIES = {
        "high": pill("H", "$error-muted", "$text-error"),
        "medium": pill("M", "$warning-muted", "$text-warning"),
        "low": pill("L", "$primary-muted", "$text-primary"),
    }

    def __init__(
        self,
        entries: list[Entry] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        self.newly_completed: set[Plan.Entry] = set()
        super().__init__(name=name, id=id, classes=classes)
        self.set_reactive(Plan.entries, entries or [])

    def watch_entries(
        self,
        old_entries: list[Entry] | None,
        new_entries: list[Entry] | None,
    ) -> None:
        old_map = {e.content: e for e in (old_entries or [])}
        newly: set[Plan.Entry] = set()
        for entry in new_entries or []:
            prev = old_map.get(entry.content)
            if (
                prev is not None
                and entry.status == "completed"
                and prev.status != "completed"
            ):
                newly.add(entry)
        self.newly_completed = newly
        self.all_complete = bool(new_entries) and all(
            e.status == "completed" for e in new_entries
        )

    def compose(self) -> ComposeResult:
        if not self.entries:
            yield Static("No plan yet", classes="-no-plan")
            return
        for entry in self.entries:
            classes = f"priority-{entry.priority} status-{entry.status}"
            yield NonSelectableStatic(
                self._render_status(entry.status),
                classes=f"status {classes}",
            )
            strike = StrikeText(entry.content, classes=f"plan {classes}")
            yield strike
            if entry in self.newly_completed:
                self.call_after_refresh(strike.strike)
            elif entry.status == "completed":
                strike.add_class("-complete")

    @staticmethod
    def _render_status(status: str) -> Content:
        match status:
            case "completed":
                return Content(" ✔ ")
            case "in_progress":
                return Content("👉 ")
            case "pending":
                return Content(" • ")
            case _:
                return Content("   ")


    def set_todos(self, todos: list[dict[str, Any]]) -> None:
        """Replace entries from a list of `{content, status, priority}` dicts."""
        self.entries = [
            Plan.Entry(
                content=Content(t.get("content", "")),
                priority=t.get("priority", "medium"),
                status=t.get("status", "pending"),
            )
            for t in todos
        ]
