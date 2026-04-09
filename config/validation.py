"""
Settings validation with graceful degradation.

Source: Claude Code src/utils/settings/validation.ts

Claude Code principle: Invalid settings are logged but IGNORED.
The system never crashes on bad config — it uses defaults instead.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


VALID_PERMISSION_MODES = {"default", "plan", "bypass", "accept_edits", "dont_ask"}
VALID_HOOK_TYPES = {"command", "http", "prompt", "agent"}


def validate_settings(settings: dict[str, Any]) -> list[str]:
    """
    Validate settings and return list of warnings.
    Invalid settings are NOT removed — they're just reported.
    Claude Code: graceful degradation pattern.
    """
    warnings = []

    # Validate permission mode
    perms = settings.get("permissions", {})
    if isinstance(perms, dict):
        mode = perms.get("defaultMode")
        if mode and mode not in VALID_PERMISSION_MODES:
            warnings.append(f"Invalid permission mode '{mode}'. Valid: {VALID_PERMISSION_MODES}")

        # Validate permission rules
        for behavior in ["allow", "deny", "ask"]:
            rules = perms.get(behavior, [])
            if not isinstance(rules, list):
                warnings.append(f"permissions.{behavior} must be a list, got {type(rules).__name__}")

    # Validate hooks
    hooks = settings.get("hooks", {})
    if isinstance(hooks, dict):
        for event, hook_list in hooks.items():
            if not isinstance(hook_list, list):
                warnings.append(f"hooks.{event} must be a list")
                continue
            for i, hook in enumerate(hook_list):
                if not isinstance(hook, dict):
                    warnings.append(f"hooks.{event}[{i}] must be an object")
                    continue
                hook_type = hook.get("type", "command")
                if hook_type not in VALID_HOOK_TYPES:
                    warnings.append(f"hooks.{event}[{i}].type '{hook_type}' invalid")
                if hook_type == "command" and not hook.get("command"):
                    warnings.append(f"hooks.{event}[{i}] command hook missing 'command'")
                if hook_type == "http" and not hook.get("url"):
                    warnings.append(f"hooks.{event}[{i}] http hook missing 'url'")

    # Validate model
    model = settings.get("model")
    if model is not None and not isinstance(model, str):
        warnings.append(f"model must be a string, got {type(model).__name__}")

    # Validate maxTurns
    max_turns = settings.get("maxTurns")
    if max_turns is not None:
        if not isinstance(max_turns, int) or max_turns < 1:
            warnings.append("maxTurns must be a positive integer")

    for w in warnings:
        logger.warning(f"Settings validation: {w}")

    return warnings
