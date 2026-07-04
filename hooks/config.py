"""
Hook config snapshot — captured once at startup, immutable during session.

Security: Hooks are frozen at startup to prevent TOCTOU attacks.
A user cannot modify settings.json mid-session to inject hooks.
"""

from __future__ import annotations

import copy

from core_types.hooks import HookCommand
from utils.logger import get_logger

logger = get_logger(__name__)

_snapshot: dict[str, list[HookCommand]] | None = None


def capture_hooks_snapshot(
    hooks_config: dict[str, list[HookCommand]] | None = None,
) -> None:
    """
    Capture hooks config once at startup.
    captureHooksConfigSnapshot
    """
    global _snapshot
    _snapshot = copy.deepcopy(hooks_config) if hooks_config else {}
    logger.debug(f"Hooks snapshot captured: {len(_snapshot)} events configured")


def get_hooks_from_snapshot() -> dict[str, list[HookCommand]]:
    """
    Get the frozen hooks config.
    getHooksConfigFromSnapshot
    """
    if _snapshot is None:
        return {}
    return _snapshot


def reset_hooks_snapshot() -> None:
    """Reset snapshot (testing only)."""
    global _snapshot
    _snapshot = None


def swap_hooks_snapshot(new_snapshot: dict[str, list[HookCommand]] | None):
    """Atomically replace the snapshot, returning the previous one.

    Used to save/restore around a nested Agent: a sub-agent's __aenter__ calls
    capture_hooks_snapshot() and would otherwise clobber the parent session's
    hooks for the rest of the run. The caller restores the parent's snapshot
    once the sub-agent exits.
    """
    global _snapshot
    prev = _snapshot
    _snapshot = new_snapshot
    return prev
