"""
Shared read-file-state registry.

Mirrors Claude Code's toolUseContext.readFileState (a Map<path, {timestamp,
content, isPartialView, offset, limit}>). FileRead records every read here;
FileEdit / FileWrite / NotebookEdit validate against it to enforce two rules
the tool prompts promise:

  1. Read-before-write: you must Read a file before editing/writing it.
  2. Freshness: if the file changed on disk since your last full read, you
     must Read it again before writing (prevents clobbering external edits).

Keyed by resolved absolute path. Process-global like taskRegistry/agentRegistry;
a fresh process (new session) starts empty, which is correct — read-state is
per-session in CC too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReadRecord:
    timestamp: float          # mtime of the file at read time
    content: str              # full content read (for content-equality fallback)
    is_partial_view: bool     # True if read used offset/limit (not a full read)
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
) -> None:
    """Record that `path` was read. Called by FileRead after a successful read."""
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    is_partial = offset is not None or limit is not None
    _state[_key(p)] = ReadRecord(
        timestamp=mtime,
        content=content,
        is_partial_view=is_partial,
        offset=offset,
        limit=limit,
    )


def check_freshness(path: Path | str, current_content: str | None) -> str | None:
    """
    Return an error message if `path` is not safe to write, else None.

    Mirrors CC FileEditTool.validateInput (errorCode 6 + 7):
      - never read       → "File has not been read yet. Read it first..."
      - read partially   → same (a partial view isn't enough to safely edit)
      - modified on disk since read (and content differs) →
        "File has been modified since read... Read it again..."

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
        return None  # file vanished; let the caller's own existence check handle it

    if last_write > rec.timestamp:
        # mtime moved. For a full read, fall back to content comparison so a
        # touch-without-change (cloud sync, antivirus) isn't a false positive.
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
