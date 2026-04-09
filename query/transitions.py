"""
Query loop state transitions.

Source: Claude Code src/query.ts lines 204-217, return/continue sites.

Every continue and return in the loop has an explicit reason.
This prevents infinite loops and enables debugging.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Continue:
    """The loop should continue to the next iteration."""
    reason: str
    # "tool_execution"          — normal: tools ran, need another model turn
    # "reactive_compact_retry"  — 413 error triggered compact, retry
    # "max_output_tokens_retry" — hit max tokens, retry with message


@dataclass
class Terminal:
    """The loop should stop."""
    reason: str
    # "end_turn"       — model responded without tools (success)
    # "max_turns"      — hit max_turns limit
    # "prompt_too_long" — 413 after all recovery exhausted
    # "max_output_tokens" — hit limit after 3 retries
    # "aborted"        — user cancelled
    # "error"          — unrecoverable error
    turn_count: int = 0
    error: str | None = None
