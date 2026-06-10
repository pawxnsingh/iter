"""
Hook types — all 27 hook events from Claude Code.

Source: Claude Code src/entrypoints/agentSdkTypes.ts (HOOK_EVENTS constant)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class HookEvent(str, Enum):
    """All 28 hook events (27 from Claude Code + POST_SAMPLING).

    POST_SAMPLING fires after the model has produced a complete assistant
    message but BEFORE we run any of the requested tools. Lets external
    observers (logging, tracing, content filters) see the full reply
    without being on the hot path of tool execution.
    """
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    POST_SAMPLING = "PostSampling"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    NOTIFICATION = "Notification"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    SETUP = "Setup"
    TEAMMATE_IDLE = "TeammateIdle"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    CONFIG_CHANGE = "ConfigChange"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"


@dataclass
class HookCommand:
    """A hook defined in settings."""
    type: str = "command"
    command: str = ""
    url: str = ""
    match: str | None = None
    timeout: int = 600
    block: bool = False


@dataclass
class HookResult:
    """Result from hook execution."""
    outcome: str = "success"
    blocking_error: str | None = None
    decision: str | None = None
    reason: str | None = None
    updated_input: dict[str, Any] | None = None
    system_message: str | None = None
    additional_context: str | None = None
    prevent_continuation: bool = False
