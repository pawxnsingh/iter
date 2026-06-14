"""
Shared read-file-state registry.

Mirrors the toolUseContext.readFileState (a Map<path, {timestamp,
content, isPartialView, offset, limit}>). FileRead records every read here;
FileEdit / FileWrite / NotebookEdit validate against it to enforce two rules
the tool prompts promise:

  1. Read-before-write: you must Read a file before editing/writing it.
  2. Freshness: if the file changed on disk since your last full read, you
     must Read it again before writing (prevents clobbering external edits).

Keyed by resolved absolute path. Process-global like taskRegistry/agentRegistry;
a fresh process (new session) starts empty, which is correct — read-state is
per-session in too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReadRecord:
    timestamp: float
    content: str
    is_partial_view: bool
    offset: int | None = None
    limit: int | None = None


_state: dict[str, ReadRecord] = {}


def _key(path: Path | str) -> str:
    return str(Path(path).resolve())


def record_read(
    path: Path | str,
    content: str,
    *,
    offset: int | None = None,
    limit: int | None = None,
    is_partial_view: bool = False,
) -> None:
    """Record that `path` was read. Called by FileRead after a successful read.

    parity: a normal Read — even a partial one with offset/limit — does NOT
    set is_partial_view. is_partial_view
    is reserved for INJECTED memory content that differs from disk
; Edit/Write require a real Read when they see it. We
    don't inject content, so callers leave it False, and any Read satisfies the
    read-before-write gate.
    """
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    _state[_key(p)] = ReadRecord(
        timestamp=mtime,
        content=content,
        is_partial_view=is_partial_view,
        offset=offset,
        limit=limit,
    )


def check_freshness(path: Path | str, current_content: str | None) -> str | None:
    """
    Return an error message if `path` is not safe to write, else None.

    Mirrors FileEditTool.validateInput (errorCode 6 + 7):
      - never read, or only an injected partial view →
        "File has not been read yet. Read it first..."
      - modified on disk since read (and content differs) →
        "File has been modified since read... Read it again..."

    parity: a normal partial Read (offset/limit) DOES satisfy this gate — the
    block only fires when there's no read record at all (or an injected
    partial-view record, which we don't produce).

    current_content: the file's current text (None if it doesn't exist yet —
    Edit's create-new path passes None and skips the freshness branch).
    """
    rec = _state.get(_key(path))
    if rec is None or rec.is_partial_view:
        return (
            "File has not been read yet. Read it first before writing to it."
        )

    try:
        last_write = Path(path).stat().st_mtime
    except OSError:
        return None

    if last_write > rec.timestamp:
        if current_content is not None and current_content == rec.content:
            return None
        return (
            "File has been modified since read, either by the user or by a "
            "linter. Read it again before attempting to write it."
        )
    return None


def clear() -> None:
    """Testing helper."""
    _state.clear()
