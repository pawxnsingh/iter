"""
Permission types matching Claude Code's permission system.

Claude Code source: src/types/permissions.ts

Permission modes control how tool execution is gated:
- default: ask for writes, allow reads
- plan: block ALL tool execution
- accept_edits: auto-approve edits to allowed paths
- bypass: allow everything (--dangerously-skip-permissions)
- dont_ask: auto-deny, no prompts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass"
    DONT_ASK = "dont_ask"

    @classmethod
    def _missing_(cls, value: object) -> "PermissionMode | None":
        """Accept Claude Code's camelCase variants ('acceptEdits', 'dontAsk').

        CC settings.json uses 'acceptEdits' / 'dontAsk'. We canonicalize to
        snake_case internally for Python idiom, but a CC config file should
        Just Work without renaming.
        """
        if not isinstance(value, str):
            return None
        alias = {
            "acceptEdits": cls.ACCEPT_EDITS,
            "dontAsk": cls.DONT_ASK,
        }
        return alias.get(value)


class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionResult:
    """Result of a permission check."""
    behavior: PermissionBehavior
    message: str = ""
    updated_input: dict[str, Any] | None = None
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> PermissionResult:
        return cls(behavior=PermissionBehavior.ALLOW, reason=reason)

    @classmethod
    def deny(cls, message: str, reason: str = "") -> PermissionResult:
        return cls(behavior=PermissionBehavior.DENY, message=message, reason=reason)

    @classmethod
    def ask(cls, message: str, reason: str = "") -> PermissionResult:
        return cls(behavior=PermissionBehavior.ASK, message=message, reason=reason)


@dataclass(frozen=True)
class ToolPermissionContext:
    """
    Context for permission checking.
    """
    mode: PermissionMode = PermissionMode.DEFAULT
    working_directories: tuple[str, ...] = ()
    allow_rules: dict[str, list[str]] = field(default_factory=dict)
    deny_rules: dict[str, list[str]] = field(default_factory=dict)
    ask_rules: dict[str, list[str]] = field(default_factory=dict)
