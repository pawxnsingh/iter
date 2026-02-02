"""
Custom Message classes — Textual's event bus.

Pattern (from toad/messages.py): each cross-widget event is a small dataclass
Message. Widgets post via self.post_message(X); ancestors handle via @on(X).
Keeps producers dumb and consumers decoupled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from textual.message import Message


# ─── User actions ──────────────────────────────────────────────────────────


@dataclass
class UserInputSubmitted(Message):
    """User pressed enter in the prompt."""

    body: str
    shell: bool = False


@dataclass
class SlashCommandChosen(Message):
    """User picked a command from the SlashComplete overlay."""

    command: str  # e.g. "/help"


@dataclass
class InvokeSlashComplete(Message):
    """Prompt wants to show the slash-complete overlay."""

    query: str = ""


@dataclass
class Dismiss(Message):
    """A popup / overlay wants to be dismissed."""

    @property
    def control(self):  # Textual convention for @on selectors
        return self.source if hasattr(self, "source") else None


# ─── Agent events (mirror of AgentEventType) ──────────────────────────────


@dataclass
class AgentStarted(Message):
    pass


@dataclass
class AgentEnded(Message):
    pass


@dataclass
class AgentFailed(Message):
    error: str


@dataclass
class TextDelta(Message):
    """A chunk of assistant text — appended to the in-progress AgentResponse."""

    content: str


@dataclass
class TextComplete(Message):
    """End-of-assistant-message marker."""

    content: str = ""


@dataclass
class ToolStarted(Message):
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolCompleted(Message):
    call_id: str
    name: str
    success: bool
    output: str
    error: str | None
    metadata: dict[str, Any]
    diff: str | None
    truncated: bool
    exit_code: int | None


# ─── Ambient notifications ────────────────────────────────────────────────


@dataclass
class Flash(Message):
    """Transient toast at the bottom of the conversation."""

    content: str
    style: Literal["default", "success", "warning", "error"] = "default"
    duration: float | None = None


@dataclass
class SlashResult(Message):
    """Result string from a slash command — rendered as a Note."""

    text: str


@dataclass
class RequestExit(Message):
    """Slash command asked to exit the app."""
