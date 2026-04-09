"""
QueryConfig: Immutable snapshot of runtime config taken once at query entry.

Source: Claude Code src/query/config.ts

Separates per-query immutable state from mutable loop state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryConfig:
    """Immutable config snapshot for one query() call."""
    session_id: str
    max_turns: int = 200
    context_window: int = 200_000
    model: str = "gpt-4"
