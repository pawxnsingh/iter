"""
Hook config snapshot — captured once at startup, immutable during session.

Source: Claude Code src/utils/hooks/hooksConfigSnapshot.ts

Security: Hooks are frozen at startup to prevent TOCTOU attacks.
A user cannot modify settings.json mid-session to inject hooks.
"""

from __future__ import annotations

import copy
import logging

from core_types.hooks import HookCommand

logger = logging.getLogger(__name__)

_snapshot: dict[str, list[HookCommand]] | None = None


def capture_hooks_snapshot(hooks_config: dict[str, list[HookCommand]] | None = None) -> None:
    """
    Capture hooks config once at startup.
    Claude Code: captureHooksConfigSnapshot()
    """
    global _snapshot
    _snapshot = copy.deepcopy(hooks_config) if hooks_config else {}
    logger.debug(f"Hooks snapshot captured: {len(_snapshot)} events configured")


def get_hooks_from_snapshot() -> dict[str, list[HookCommand]]:
    """
    Get the frozen hooks config.
    Claude Code: getHooksConfigFromSnapshot()
    """
    if _snapshot is None:
        return {}
    return _snapshot


def reset_hooks_snapshot() -> None:
    """Reset snapshot (testing only)."""
    global _snapshot
    _snapshot = None
