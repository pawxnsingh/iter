"""
5-layer settings merge matching Claude Code exactly.

Source: Claude Code src/utils/settings/settings.ts (8K+ lines)

Priority order (lowest to highest):
  1. /etc/ai-agent/managed-settings.json     (managed/admin policy)
  2. ~/.config/ai-agent/settings.json        (user preferences)
  3. .ai-agent/settings.json                 (project, checked in)
  4. .ai-agent/settings.local.json           (local, gitignored)
  5. CLI flags                               (runtime override)

Merge rules (exact from Claude Code settingsMergeCustomizer):
  - Arrays: later source COMPLETELY REPLACES earlier (not merged)
  - Objects: merged recursively
  - Scalars: later wins
  - Unknown fields: preserved (graceful degradation)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from core_types.hooks import HookCommand

logger = logging.getLogger(__name__)


def get_settings_file_paths(cwd: str) -> dict[str, Path]:
    """Get settings file paths for each source level."""
    return {
        "managed": Path("/etc/ai-agent/managed-settings.json"),
        "user": Path(user_config_dir("ai-agent")) / "settings.json",
        "project": Path(cwd) / ".ai-agent" / "settings.json",
        "local": Path(cwd) / ".ai-agent" / "settings.local.json",
    }


def load_settings(cwd: str, cli_overrides: dict | None = None) -> dict[str, Any]:
    """
    Load and merge settings from all 5 layers.
    Claude Code: getSettings_DEPRECATED() in settings.ts
    """
    merged: dict[str, Any] = {}
    paths = get_settings_file_paths(cwd)

    # Load in priority order (lowest first)
    for source in ["managed", "user", "project", "local"]:
        path = paths[source]
        settings = _load_settings_file(path)
        if settings:
            merged = deep_merge(merged, settings)
            logger.debug(f"Loaded settings from {source}: {path}")

    # CLI overrides (highest priority)
    if cli_overrides:
        merged = deep_merge(merged, cli_overrides)

    return merged


def deep_merge(base: dict, override: dict) -> dict:
    """
    Merge strategy matching Claude Code's settingsMergeCustomizer.
    - Arrays: override REPLACES base entirely (not appended)
    - Objects: merged recursively
    - Scalars: override wins
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            # Arrays and scalars: replace entirely
            result[key] = value
    return result


def _load_settings_file(path: Path) -> dict[str, Any] | None:
    """Load a single settings JSON file with graceful degradation."""
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        # Handle BOM (Claude Code does this for Windows)
        if content.startswith("\ufeff"):
            content = content[1:]
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning(f"Settings file is not an object: {path}")
            return None
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in settings: {path}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error reading settings: {path}: {e}")
        return None


def extract_hooks_from_settings(settings: dict[str, Any]) -> dict[str, list[HookCommand]]:
    """
    Extract hook configurations from settings.
    Claude Code: hooks section in settings.json
    """
    hooks_config: dict[str, list[HookCommand]] = {}
    hooks_raw = settings.get("hooks", {})

    if not isinstance(hooks_raw, dict):
        return hooks_config

    for event_name, hook_list in hooks_raw.items():
        if not isinstance(hook_list, list):
            continue
        commands = []
        for hook_def in hook_list:
            if not isinstance(hook_def, dict):
                continue
            commands.append(HookCommand(
                type=hook_def.get("type", "command"),
                command=hook_def.get("command", ""),
                url=hook_def.get("url", ""),
                match=hook_def.get("match"),
                timeout=hook_def.get("timeout", 600),
                block=hook_def.get("block", False),
            ))
        if commands:
            hooks_config[event_name] = commands

    return hooks_config


def extract_permission_rules(
    settings: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    """
    Extract permission rules from CC-shape settings.json and parse them into
    the runtime context shape used by PermissionChecker.

    Input (CC settings.json):
      {"permissions": {"allow": ["Bash(echo *)", "Read"],
                       "deny":  ["Bash(rm *)"],
                       "ask":   ["Write(/etc/*)"]}}

    Output (matches ToolPermissionContext.{allow,deny,ask}_rules):
      {"allow": {"Bash": ["echo *"], "Read": [""]},
       "deny":  {"Bash": ["rm *"]},
       "ask":   {"Write": ["/etc/*"]}}

    Each `Tool(content)` rule string is parsed via CC's exact algorithm
    (parse_permission_rule_string), then grouped by tool name. Tool-wide
    rules ("Bash") become an empty-string pattern that matches any call.
    """
    from permissions.rules import parse_rule_strings_to_dict

    perms = settings.get("permissions", {})
    if not isinstance(perms, dict):
        return {"allow": {}, "deny": {}, "ask": {}}

    out: dict[str, dict[str, list[str]]] = {"allow": {}, "deny": {}, "ask": {}}
    for behavior in ("allow", "deny", "ask"):
        raw = perms.get(behavior, [])
        if isinstance(raw, list):
            out[behavior] = parse_rule_strings_to_dict(raw)
    return out


def save_settings(path: Path, settings: dict[str, Any]) -> None:
    """Save settings to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
