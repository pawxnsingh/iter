"""
Pick a previous session to resume — DataTable picker.

Adapts toad's SessionResumeModal pattern to OUR session storage
(`session.storage.SessionStorage`), which lists sessions from disk via
JSONL transcripts rather than a SQLite DB.

Returns the selected session's metadata dict, or None on cancel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, HorizontalGroup, VerticalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Markdown, Static


HELP = """\
# Resume session

Pick a session below to restore its conversation context.
"""


def friendly_time_ago(iso_or_seconds) -> str:
    """Render a timestamp as 'just now' / 'N minutes ago' / etc."""
    try:
        if isinstance(iso_or_seconds, (int, float)):
            past = datetime.fromtimestamp(float(iso_or_seconds), tz=timezone.utc)
        else:
            past = datetime.fromisoformat(str(iso_or_seconds))
    except Exception:
        return str(iso_or_seconds)

    now = datetime.now(timezone.utc) if past.tzinfo else datetime.now()
    secs = (now - past).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = int(secs // 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if secs < 86400:
        h = int(secs // 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    local = past.astimezone() if past.tzinfo else past
    return local.strftime("%c")


class SessionResumeModal(ModalScreen):
    DEFAULT_CSS = """
    SessionResumeModal { align: center middle; }
    #container {
        width: 90%;
        max-width: 120;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    .warning {
        margin: 0 0 1 0;
        padding: 0 1;
        color: $text-warning;
        background: $warning 10%;
        border: round $warning 60%;
    }
    #table-container {
        height: 12;
        margin: 0 0 1 0;
    }
    DataTable { height: 12; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        """sessions = [{session_id, project_dir, updated_at, turn_count, ...}]"""
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        with VerticalGroup(id="container"):
            yield Markdown(HELP)
            if not self._sessions:
                yield Static("No previous sessions.", classes="warning")
            with Center(id="table-container"):
                yield DataTable(id="sessions", cursor_type="row")
            with HorizontalGroup(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Resume", variant="primary", id="resume", disabled=True)

    def on_mount(self) -> None:
        table = self.query_one("#sessions", DataTable)
        table.add_columns("Session", "Updated", "Turns", "Project")
        for s in self._sessions[:50]:
            sid = str(s.get("session_id", ""))[:8] + "…"
            updated = friendly_time_ago(s.get("updated_at", 0))
            turns = str(s.get("turn_count", 0))
            project = str(s.get("project_dir", ""))[-50:]
            table.add_row(sid, updated, turns, project, key=str(s.get("session_id", "")))

    @on(DataTable.RowHighlighted)
    def _on_row_highlighted(self) -> None:
        try:
            self.query_one("#resume", Button).disabled = False
        except Exception:
            pass

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        self._dismiss_with(event.row_key.value)

    @on(Button.Pressed, "#resume")
    def _on_resume(self) -> None:
        table = self.query_one("#sessions", DataTable)
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return
        if row_key is None or row_key.value is None:
            return
        self._dismiss_with(row_key.value)

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)

    def _dismiss_with(self, session_id: str) -> None:
        for s in self._sessions:
            if str(s.get("session_id", "")) == session_id:
                self.dismiss(s)
                return
        self.dismiss(None)

    def action_dismiss(self, result=None) -> None:
        self.dismiss(None)
