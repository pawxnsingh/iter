"""
Permission Checker: Full mode-based permission system.

Source: Claude Code src/utils/permissions/permissions.ts (lines 1158-1319)

Flow (exact from Claude Code hasPermissionsToUseToolInner):
  Stage 1: Rule & Tool-Level Checks (pre-mode)
    1a. Tool denied? → DENY
    1b. Tool asks? → ASK (unless sandboxed)
    1c. tool.check_permissions() → can return allow/deny/ask
    1d. Safety checks bypass-immune → ASK
  Stage 2: Mode-Based Decision
    2a. bypass mode → ALLOW
    2b. Tool always-allowed → ALLOW
  Stage 3: Convert passthrough → ASK
  Post-processing:
    - dont_ask mode: ASK → DENY
    - accept_edits: auto-allow filesystem commands in working dir
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from core_types.permissions import (
    PermissionMode,
    PermissionResult,
    ToolPermissionContext,
)
from permissions.filesystem import (
    get_paths_for_permission_check,
    is_sensitive_path,
    path_in_working_directory,
)
from permissions.rules import match_rules_for_tool

logger = logging.getLogger(__name__)

# Commands auto-allowed in accept_edits mode (Claude Code: modeValidation.ts)
ACCEPT_EDITS_COMMANDS = {"mkdir", "touch", "rm", "rmdir", "mv", "cp", "sed"}


class PermissionChecker:
    """
    Full permission checker matching Claude Code's 3-stage flow.
    """

    def __init__(
        self,
        context: ToolPermissionContext,
        prompt_fn: Callable[[str, str, dict], Awaitable[bool]] | None = None,
    ):
        self.context = context
        self.prompt_fn = prompt_fn

    async def check(self, tool, params: dict) -> PermissionResult:
        """
        Check if a tool call is permitted.
        Mirrors Claude Code's hasPermissionsToUseToolInner().
        """
        # ══════════════════════════════════════════════════════════
        # STAGE 1: Rule & Tool-Level Checks (pre-mode)
        # ══════════════════════════════════════════════════════════

        # 1a. Tool denied by deny rules?
        deny_matches = match_rules_for_tool(tool.name, self.context.deny_rules)
        if deny_matches:
            return PermissionResult.deny(
                f"Denied by rule: {deny_matches[0]}",
                reason="rule:deny",
            )

        # 1b. Tool has ask rules?
        ask_matches = match_rules_for_tool(tool.name, self.context.ask_rules)

        # 1c. Safety check: sensitive paths (bypass-immune)
        path = getattr(tool, 'get_path', lambda p: None)(params)
        if path:
            all_paths = get_paths_for_permission_check(path)
            for p in all_paths:
                if is_sensitive_path(p):
                    return PermissionResult.ask(
                        f"Accessing sensitive path: {p}",
                        reason="safety_check",
                    )

        # 1d. Destructive tool check (bypass-immune in Claude Code)
        if hasattr(tool, 'is_destructive') and tool.is_destructive(params):
            return PermissionResult.ask(
                f"DESTRUCTIVE: {tool.name} — {self._describe_action(tool, params)}",
                reason="destructive",
            )

        # ══════════════════════════════════════════════════════════
        # STAGE 2: Mode-Based Decision
        # ══════════════════════════════════════════════════════════

        # 2a. Plan mode → block all tool execution
        if self.context.mode == PermissionMode.PLAN:
            return PermissionResult.deny(
                "Plan mode: all tool execution blocked. Use /plan to exit.",
                reason="mode:plan",
            )

        # 2b. Bypass mode → allow all
        if self.context.mode == PermissionMode.BYPASS:
            return PermissionResult.allow(reason="mode:bypass")

        # 2c. Read-only tools auto-allowed in default mode
        if tool.is_read_only(params):
            return PermissionResult.allow(reason="read_only")

        # 2d. Accept edits mode — auto-allow filesystem commands in working dirs
        if self.context.mode == PermissionMode.ACCEPT_EDITS:
            if self._is_accept_edits_allowed(tool, params):
                return PermissionResult.allow(reason="mode:accept_edits")

        # 2e. Dont ask mode — auto-deny unmatched
        if self.context.mode == PermissionMode.DONT_ASK:
            # Check allow rules first
            allow_matches = match_rules_for_tool(tool.name, self.context.allow_rules)
            if allow_matches:
                return PermissionResult.allow(reason="rule:allow")
            return PermissionResult.deny(
                "Auto-denied (restricted mode)",
                reason="mode:dont_ask",
            )

        # 2f. Check allow rules
        allow_matches = match_rules_for_tool(tool.name, self.context.allow_rules)
        if allow_matches:
            return PermissionResult.allow(reason="rule:allow")

        # ══════════════════════════════════════════════════════════
        # STAGE 3: Default → ASK
        # ══════════════════════════════════════════════════════════

        if ask_matches:
            return PermissionResult.ask(
                f"Allow {tool.name}? (matched rule: {ask_matches[0]})",
                reason="rule:ask",
            )

        return PermissionResult.ask(
            f"Allow {tool.name}?",
            reason="default:ask",
        )

    def _is_accept_edits_allowed(self, tool, params: dict) -> bool:
        """
        Check if tool is auto-allowed in accept_edits mode.
        Claude Code: checkPermissionMode() in modeValidation.ts
        """
        # File tools (Edit, Write) — check if in working directory
        path = getattr(tool, 'get_path', lambda p: None)(params)
        if path and path_in_working_directory(path, self.context.working_directories):
            return True

        # Bash commands — check if command is in ACCEPT_EDITS_COMMANDS
        if tool.name == "Bash":
            command = params.get("command", "").strip()
            if command:
                import shlex
                try:
                    tokens = shlex.split(command)
                    base_cmd = tokens[0].split("/")[-1] if tokens else ""
                    if base_cmd in ACCEPT_EDITS_COMMANDS:
                        return True
                except ValueError:
                    pass

        return False

    def _describe_action(self, tool, params: dict) -> str:
        """Generate a brief description of what the tool will do."""
        if tool.name == "Bash":
            cmd = params.get("command", "")
            return cmd[:80] + ("..." if len(cmd) > 80 else "")
        if tool.name in ("Edit", "Write"):
            return params.get("path", params.get("file_path", ""))
        return str(params)[:80]
