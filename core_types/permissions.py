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


class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionRuleSource(str, Enum):
    """Where a permission rule originated. Priority order (lowest to highest)."""
    MANAGED = "managed"
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"
    CLI = "cli"
    SESSION = "session"


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
    Context for permission checking. Mirrors Claude Code's ToolPermissionContext.
    Immutable (frozen) -- same as Claude Code's DeepImmutable<>.
    """
    mode: PermissionMode = PermissionMode.DEFAULT
    working_directories: tuple[str, ...] = ()
    allow_rules: dict[str, list[str]] = field(default_factory=dict)
    deny_rules: dict[str, list[str]] = field(default_factory=dict)
    ask_rules: dict[str, list[str]] = field(default_factory=dict)
