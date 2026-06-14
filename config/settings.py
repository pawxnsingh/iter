"""
5-layer settings merge matching.

Priority order (lowest to highest) — SETTING_SOURCES, "later overrides
earlier", policy LAST so admin/managed settings are un-overridable:
  1. ~/.config/ai-agent/settings.json        (user preferences)
  2. .ai-agent/settings.json                 (project, checked in)
  3. .ai-agent/settings.local.json           (local, gitignored)
  4. CLI flags                               (--settings flag layer)
  5. /etc/ai-agent/managed-settings.json     (managed/admin policy — HIGHEST)

Merge rules:
  - Arrays: CONCATENATE + dedupe (so permission allow/deny rules from every
    layer coexist; a higher layer does NOT wipe a lower layer's rules)
  - Objects: merged recursively
  - Scalars: later wins
  - Unknown fields: preserved (graceful degradation)
"""

from __future__ import annotations

import json
import logging
import os
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
    Load and merge settings across all layers.

    Order is SETTING_SOURCES: user → project → local → flag(CLI) → policy
    (managed). Later overrides earlier, so MANAGED is highest and cannot be
    overridden by user/project/local — the admin-policy guarantee.
    """
    merged: dict[str, Any] = {}
    paths = get_settings_file_paths(cwd)

    for source in ["user", "project", "local"]:
        settings = _load_settings_file(paths[source])
        if settings:
            merged = deep_merge(merged, settings)
            logger.debug(f"Loaded settings from {source}: {paths[source]}")

    if cli_overrides:
        merged = deep_merge(merged, cli_overrides)

    managed = _load_settings_file(paths["managed"])
    if managed:
        merged = deep_merge(merged, managed)
        logger.debug(f"Loaded managed (policy) settings: {paths['managed']}")

    return merged


def _dedupe_preserving_order(items: list) -> list:
    """Dedupe a list, preserving first-seen order."""
    seen: set = set()
    out: list = []
    for it in items:
        key = json.dumps(it, sort_keys=True) if isinstance(it, (dict, list)) else it
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def deep_merge(base: dict, override: dict) -> dict:
    """
    Merge strategy matching the settingsMergeCustomizer.
    - Arrays: CONCATENATE base + override, then dedupe (NOT replace) — so
      permission rules / dirs / env-arrays from all layers coexist.
    - Objects: merged recursively
    - Scalars: override wins
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] = _dedupe_preserving_order(result[key] + value)
        else:
            result[key] = value
    return result


def _load_settings_file(path: Path) -> dict[str, Any] | None:
    """Load a single settings JSON file with graceful degradation."""
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
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
    Extract hooks from -shape settings.json.

    The shape is NESTED: each event maps to an array of MATCHER GROUPS, and the
    commands live one level down under `hooks`:

      {"hooks": {"PreToolUse": [
          {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "..."}]}
      ]}}

    The group's `matcher` is attached to every HookCommand it contains so the
    executor can filter by tool name. (A flat top-level command — the old, wrong
    shape — is also tolerated for graceful degradation.)
    """
    hooks_config: dict[str, list[HookCommand]] = {}
    hooks_raw = settings.get("hooks", {})

    if not isinstance(hooks_raw, dict):
        return hooks_config

    for event_name, matcher_groups in hooks_raw.items():
        if not isinstance(matcher_groups, list):
            continue
        commands: list[HookCommand] = []
        for group in matcher_groups:
            if not isinstance(group, dict):
                continue
            inner = group.get("hooks")
            if isinstance(inner, list):
                matcher = group.get("matcher")
                for hook_def in inner:
                    if isinstance(hook_def, dict):
                        commands.append(_hook_command(hook_def, matcher))
            elif "command" in group or "url" in group:
                commands.append(_hook_command(group, group.get("matcher")))
        if commands:
            hooks_config[event_name] = commands

    return hooks_config


def _hook_command(hook_def: dict, matcher: str | None) -> HookCommand:
    return HookCommand(
        type=hook_def.get("type", "command"),
        command=hook_def.get("command", ""),
        url=hook_def.get("url", ""),
        match=matcher,
        timeout=hook_def.get("timeout", 600),
        block=hook_def.get("block", False),
    )


def apply_settings_env(settings: dict[str, Any]) -> None:
    """Apply settings.json `env` to the process environment.

    OVERRIDES process.env with settings.env (Object.assign(process.env, env),
    export. (additionally trust-tiers project/local env to a safe allowlist;
    we apply the already-merged env wholesale, which is the override direction
    that matters for parity.)
    """
    env = settings.get("env")
    if not isinstance(env, dict):
        return
    for key, value in env.items():
        if value is not None:
            os.environ[str(key)] = str(value)


def get_default_permission_mode(settings: dict[str, Any]) -> str | None:
    """Read permissions.defaultMode. None if unset."""
    perms = settings.get("permissions")
    if isinstance(perms, dict):
        mode = perms.get("defaultMode")
        if isinstance(mode, str):
            return mode
    return None


def extract_permission_rules(
    settings: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    """
    Extract permission rules from -shape settings.json and parse them into
    the runtime context shape used by PermissionChecker.

    Input:
      {"permissions": {"allow": ["Bash(echo *)", "Read"],
                       "deny":  ["Bash(rm *)"],
                       "ask":   ["Write(/etc/*)"]}}

    Output (matches ToolPermissionContext.{allow,deny,ask}_rules):
      {"allow": {"Bash": ["echo *"], "Read": [""]},
       "deny":  {"Bash": ["rm *"]},
       "ask":   {"Write": ["/etc/*"]}}

    Each `Tool(content)` rule string is parsed via the exact algorithm
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
