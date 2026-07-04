"""
Permission Checker: Full mode-based permission system.

Flow:
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

import os
from typing import Awaitable, Callable

from core_types.permissions import (
    PermissionMode,
    PermissionResult,
    ToolPermissionContext,
)
from permissions.filesystem import (
    is_protected_auto_edit_path,
    path_in_working_directory,
)
from permissions.rules import match_rules_for_tool
from utils.logger import get_logger

logger = get_logger(__name__)

ACCEPT_EDITS_COMMANDS = {"mkdir", "touch", "rm", "rmdir", "mv", "cp", "sed"}


class PermissionChecker:
    """
    Full permission checker matching the 3-stage flow.
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
        Mirrors the hasPermissionsToUseToolInner.
        """

        def _ask(message: str, reason: str) -> PermissionResult:
            if self.context.mode == PermissionMode.DONT_ASK:
                return PermissionResult.deny(
                    f"{tool.name} requires confirmation; auto-denied in dont_ask mode.",
                    reason="mode:dont_ask",
                )
            return PermissionResult.ask(message, reason=reason)

        deny_matches = match_rules_for_tool(
            tool.name, self.context.deny_rules, _pv(tool, params)
        )
        if not deny_matches and tool.name == "Bash":
            from permissions.bash_security import split_and_normalize_command

            for seg in split_and_normalize_command(params.get("command", "")):
                seg_match = match_rules_for_tool("Bash", self.context.deny_rules, seg)
                if seg_match:
                    deny_matches = seg_match
                    break
        if deny_matches:
            return PermissionResult.deny(
                f"Denied by rule: {deny_matches[0]}",
                reason="rule:deny",
            )

        ask_matches = match_rules_for_tool(
            tool.name, self.context.ask_rules, _pv(tool, params)
        )
        if not ask_matches and tool.name == "Bash":
            from permissions.bash_security import split_and_normalize_command

            for seg in split_and_normalize_command(params.get("command", "")):
                seg_match = match_rules_for_tool("Bash", self.context.ask_rules, seg)
                if seg_match:
                    ask_matches = seg_match
                    break

        if not tool.is_read_only(params):
            wpath = getattr(tool, "get_path", lambda p: None)(params)
            if wpath and is_protected_auto_edit_path(wpath):
                return _ask(
                    f"Editing a protected config/VCS path: {wpath}",
                    reason="protected_path",
                )

        cp = getattr(tool, "check_permissions", None)
        if callable(cp):
            try:
                tool_decision = cp(params, self.context.mode)
            except Exception:
                tool_decision = None
            if tool_decision:
                behavior, msg = tool_decision
                if behavior == "deny":
                    return PermissionResult.deny(msg, reason="tool:check")
                if behavior == "ask":
                    return _ask(msg, reason="tool:check")
                if behavior == "allow":
                    return PermissionResult.allow(reason="tool:check")

        if self.context.mode == PermissionMode.BYPASS:
            return PermissionResult.allow(reason="mode:bypass")

        if tool.is_read_only(params):
            rd_path = getattr(tool, "get_path", lambda p: None)(params)
            work_dirs = self.context.working_directories or (os.getcwd(),)
            if rd_path and not os.path.isabs(rd_path):
                rd_path = os.path.join(work_dirs[0], rd_path)
            in_cwd = not rd_path or path_in_working_directory(rd_path, work_dirs)
            if in_cwd and not ask_matches:
                return PermissionResult.allow(reason="read_only")
            if match_rules_for_tool(
                tool.name, self.context.allow_rules, _pv(tool, params)
            ):
                return PermissionResult.allow(reason="rule:allow")
            if ask_matches:
                return _ask(
                    f"Allow {tool.name}? (matched rule: {ask_matches[0]})",
                    reason="rule:ask",
                )
            return _ask(
                f"Read outside working directory: {rd_path}",
                reason="read_outside_cwd",
            )

        if self.context.mode == PermissionMode.PLAN:
            return PermissionResult.deny(
                "Plan mode: write/mutate tools blocked. Use ExitPlanMode to exit.",
                reason="mode:plan",
            )

        if self.context.mode == PermissionMode.ACCEPT_EDITS:
            if self._is_accept_edits_allowed(tool, params) and not ask_matches:
                return PermissionResult.allow(reason="mode:accept_edits")

        allow_matches = match_rules_for_tool(
            tool.name, self.context.allow_rules, _pv(tool, params)
        )
        if allow_matches:
            return PermissionResult.allow(reason="rule:allow")

        if ask_matches:
            return _ask(
                f"Allow {tool.name}? (matched rule: {ask_matches[0]})",
                reason="rule:ask",
            )

        return _ask(
            f"Allow {tool.name}?",
            reason="default:ask",
        )

    def check_rules_only(self, tool, params: dict) -> PermissionResult | None:
        """Rule-only permission check.

        Used when a PreToolUse hook returned permissionDecision:'allow' — the hook
        bypasses the default prompt and mode logic, but explicit deny/ask RULES
        still apply. Returns a DENY/ASK PermissionResult if a rule matches, else
        None (meaning: allow). Honors Bash compound-command deny segments.
        """
        deny_matches = match_rules_for_tool(
            tool.name, self.context.deny_rules, _pv(tool, params)
        )
        if not deny_matches and tool.name == "Bash":
            from permissions.bash_security import split_and_normalize_command

            for seg in split_and_normalize_command(params.get("command", "")):
                seg_match = match_rules_for_tool("Bash", self.context.deny_rules, seg)
                if seg_match:
                    deny_matches = seg_match
                    break
        if deny_matches:
            return PermissionResult.deny(
                f"Denied by rule: {deny_matches[0]}", reason="rule:deny"
            )

        ask_matches = match_rules_for_tool(
            tool.name, self.context.ask_rules, _pv(tool, params)
        )
        if ask_matches:
            return PermissionResult.ask(
                f"Allow {tool.name}? (matched rule: {ask_matches[0]})",
                reason="rule:ask",
            )
        return None

    def _is_accept_edits_allowed(self, tool, params: dict) -> bool:
        """
        Check if tool is auto-allowed in accept_edits mode.
        """
        path = getattr(tool, "get_path", lambda p: None)(params)
        if path and path_in_working_directory(path, self.context.working_directories):
            return True

        if tool.name == "Bash":
            command = params.get("command", "").strip()
            if command:
                from permissions.bash_security import split_and_normalize_command

                segments = split_and_normalize_command(command)
                if not segments:
                    return False
                import shlex

                for seg in segments:
                    try:
                        tokens = shlex.split(seg)
                    except ValueError:
                        return False
                    base = tokens[0].split("/")[-1] if tokens else ""
                    if base not in ACCEPT_EDITS_COMMANDS:
                        return False
                return True

        return False

    def _describe_action(self, tool, params: dict) -> str:
        """Generate a brief description of what the tool will do."""
        if tool.name == "Bash":
            cmd = params.get("command", "")
            return cmd[:80] + ("..." if len(cmd) > 80 else "")
        if tool.name in ("Edit", "Write"):
            return params.get("file_path", "")
        return str(params)[:80]


def _pv(tool, params: dict) -> str | None:
    """Resolve a tool's permission value for sub-pattern rule matching.
    Defaults to None when a tool doesn't implement get_permission_value."""
    fn = getattr(tool, "get_permission_value", None)
    if not callable(fn):
        return None
    try:
        return fn(params)
    except Exception:
        return None
