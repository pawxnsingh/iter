"""
Tool-related types matching Claude Code's tool system.

Claude Code source: src/Tool.ts, src/types/tools.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


@dataclass
class ToolUseContext:
    """
    All context a tool needs during execution.
    Mirrors Claude Code's ToolUseContext.
    """

    cwd: Path
    model: str
    context_window: int
    session_id: str
    debug: bool = False
    turn_count: int = 0

    # Permission context (imported separately to avoid circular)
    # permission_context: ToolPermissionContext  -- set at runtime

    # Callback for interactive permission dialogs
    request_permission: Callable[..., Awaitable[bool]] | None = None


@dataclass
class ValidationResult:
    """Result of tool input validation."""

    valid: bool
    message: str = ""
    error_code: int = 0

    @classmethod
    def ok(cls) -> ValidationResult:
        return cls(valid=True)

    @classmethod
    def fail(cls, message: str, error_code: int = 1) -> ValidationResult:
        return cls(valid=False, message=message, error_code=error_code)
