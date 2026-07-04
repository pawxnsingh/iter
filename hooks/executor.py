"""
Hook Executor: Pre/post tool use hooks from configuration.

Hooks are user-defined commands that run at lifecycle points.
They communicate back via JSON on stdout.

Hook output protocol:
  {
    "decision": "allow" | "deny",
    "reason": "...",
    "updatedInput": { ... },
    "blockingError": "...",
    "additionalContext": "..."
  }

Non-JSON stdout is treated as plain text additional context.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from core_types.hooks import HookCommand, HookEvent, HookResult
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_HOOK_TIMEOUT = 600
SESSION_END_HOOK_TIMEOUT_SEC = 1.5

_EVENT_MATCH_QUERY_FIELD = {
    "SessionStart": "source",
    "Setup": "trigger",
    "PreCompact": "trigger",
    "PostCompact": "trigger",
    "Notification": "notification_type",
    "SessionEnd": "reason",
    "StopFailure": "error",
    "SubagentStart": "agent_type",
    "SubagentStop": "agent_type",
    "Elicitation": "mcp_server_name",
    "ElicitationResult": "mcp_server_name",
    "ConfigChange": "source",
    "InstructionsLoaded": "load_reason",
    "PermissionRequest": "tool_name",
    "PermissionDenied": "tool_name",
}


def _session_end_timeout_sec() -> float:
    raw = os.environ.get("SESSIONEND_HOOKS_TIMEOUT_MS")
    if raw:
        try:
            return int(raw) / 1000
        except ValueError:
            pass
    return SESSION_END_HOOK_TIMEOUT_SEC


_GHA_SUBPROCESS_SCRUB = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_CUSTOM_HEADERS",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RUNTIME_URL",
    "ALL_INPUTS",
    "OVERRIDE_GITHUB_TOKEN",
    "DEFAULT_WORKFLOW_TOKEN",
    "SSH_SIGNING_KEY",
)


def _is_env_truthy(value: str | None) -> bool:
    """Only '1'/'true'/'yes'/'on' (case-insensitive) are truthy — so setting
    the scrub env var to 0 does NOT enable scrubbing."""
    if not value:
        return False
    return value.lower().strip() in ("1", "true", "yes", "on")


def _filter_env_secrets(environ: dict[str, str]) -> dict[str, str]:
    """Build the hook subprocess env.

    Passes the FULL environment by default; only when SUBPROCESS_ENV_SCRUB is
    truthy does it delete the _GHA_SUBPROCESS_SCRUB names (and their
    INPUT_<name> GitHub-Actions duplicates).
    """
    env = dict(environ)
    if not _is_env_truthy(os.environ.get("SUBPROCESS_ENV_SCRUB")):
        return env
    for k in _GHA_SUBPROCESS_SCRUB:
        env.pop(k, None)
        env.pop(f"INPUT_{k}", None)
    return env


_LEGACY_TOOL_NAME_ALIASES = {
    "Task": "Agent",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput",
    "BashOutputTool": "TaskOutput",
}


def _normalize_legacy_tool_name(name: str) -> str:
    return _LEGACY_TOOL_NAME_ALIASES.get(name, name)


def _get_legacy_tool_names(canonical: str) -> list[str]:
    return [legacy for legacy, c in _LEGACY_TOOL_NAME_ALIASES.items() if c == canonical]


def _matches_pattern(value: str, matcher: str | None) -> bool:
    """matchesPattern.

    empty/'*' → matches all; ^[A-Za-z0-9_|]+$ → exact or pipe-separated exact
    (e.g. "Edit|Write"), with legacy tool-name aliases normalized; anything else
    → regex, also tested against the value's legacy aliases (so "^Task$" matches
    the Agent tool).
    """
    if not matcher or matcher == "*":
        return True
    if re.fullmatch(r"[A-Za-z0-9_|]+", matcher):
        patterns = [_normalize_legacy_tool_name(p.strip()) for p in matcher.split("|")]
        return value in patterns
    try:
        regex = re.compile(matcher)
        if regex.search(value):
            return True
        return any(regex.search(legacy) for legacy in _get_legacy_tool_names(value))
    except re.error:
        return False


def _parse_hook_json(data: dict) -> HookResult:
    """Normalize hook stdout JSON across all output channels."""
    hso = data.get("hookSpecificOutput") or {}
    if not isinstance(hso, dict):
        hso = {}

    blocking = data.get("blockingError")
    decision = data.get("decision")
    permission_decision = hso.get("permissionDecision")
    reason = data.get("reason") or hso.get("permissionDecisionReason")
    additional = data.get("additionalContext") or hso.get("additionalContext")
    updated = data.get("updatedInput") or hso.get("updatedInput")

    if not blocking and (
        decision in ("block", "deny") or permission_decision == "deny"
    ):
        blocking = reason or "Operation blocked by hook"

    prevent = bool(data.get("preventContinuation", False))
    if data.get("continue") is False:
        prevent = True

    requires_ask = permission_decision == "ask"

    perm_decision = permission_decision
    if perm_decision is None:
        if decision == "approve":
            perm_decision = "allow"
        elif decision in ("block", "deny"):
            perm_decision = "deny"

    return HookResult(
        outcome="blocking" if blocking else "success",
        blocking_error=blocking,
        decision=decision,
        reason=reason,
        updated_input=updated,
        system_message=data.get("systemMessage"),
        additional_context=additional,
        prevent_continuation=prevent,
        requires_ask=requires_ask,
        permission_decision=perm_decision,
    )


class HookExecutor:
    """
    Executes hooks defined in configuration.
    """

    def __init__(self, hooks_config: dict[str, list[HookCommand]] | None = None):
        self._hooks = hooks_config or {}
        self._base_context: dict[str, Any] = {}

    def set_base_context(self, **fields: Any) -> None:
        """Provide the session-level fields includes in every hook input."""
        self._base_context = {k: v for k, v in fields.items() if v is not None}

    def has_hooks(self, event: str) -> bool:
        return bool(self._hooks.get(event))

    async def run_pre_tool_hooks(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        session_id: str = "",
        cwd: Path | str = "",
        tool_use_id: str = "",
        transcript_path: str = "",
        permission_mode: str | None = None,
    ) -> HookResult:
        """Run PreToolUse hooks. Can block execution or modify input. The stdin
        payload carries the base fields plus hook_event_name, tool_name,
        tool_input, and tool_use_id."""
        cwd = str(cwd)
        hooks = self._hooks.get(HookEvent.PRE_TOOL_USE, [])
        result = HookResult()

        for hook in hooks:
            if not _matches_pattern(tool_name, hook.match):
                continue

            hook_result = await self._execute_hook(
                hook,
                {
                    "hook_event_name": HookEvent.PRE_TOOL_USE.value,
                    "session_id": session_id,
                    "transcript_path": transcript_path,
                    "cwd": cwd,
                    "permission_mode": permission_mode,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_use_id": tool_use_id,
                },
            )

            if hook_result.blocking_error:
                return hook_result

            if hook_result.updated_input:
                result.updated_input = hook_result.updated_input

            if hook_result.requires_ask:
                result.requires_ask = True

            if hook_result.permission_decision:
                result.permission_decision = hook_result.permission_decision

            if hook_result.decision:
                result.decision = hook_result.decision
                result.reason = hook_result.reason

        return result

    async def run_post_tool_hooks(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str,
        session_id: str = "",
        cwd: Path | str = "",
        tool_use_id: str = "",
        transcript_path: str = "",
        permission_mode: str | None = None,
    ) -> HookResult:
        """Run PostToolUse hooks. Can add context or stop continuation."""
        cwd = str(cwd)
        hooks = self._hooks.get(HookEvent.POST_TOOL_USE, [])
        result = HookResult()
        contexts: list[str] = []

        for hook in hooks:
            if not _matches_pattern(tool_name, hook.match):
                continue

            hook_result = await self._execute_hook(
                hook,
                {
                    "hook_event_name": HookEvent.POST_TOOL_USE.value,
                    "session_id": session_id,
                    "transcript_path": transcript_path,
                    "cwd": cwd,
                    "permission_mode": permission_mode,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_use_id": tool_use_id,
                    "tool_response": tool_output,
                },
            )

            if hook_result.blocking_error:
                result.blocking_error = hook_result.blocking_error
            if hook_result.prevent_continuation:
                result.prevent_continuation = True

            if hook_result.additional_context:
                contexts.append(hook_result.additional_context)

        if contexts:
            result.additional_context = "\n".join(contexts)
        return result

    async def run_post_tool_failure_hooks(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        error: str,
        session_id: str = "",
        cwd: Path | str = "",
        tool_use_id: str = "",
        transcript_path: str = "",
        permission_mode: str | None = None,
        is_interrupt: bool = False,
    ) -> HookResult:
        """Run PostToolUseFailure hooks — fired when a tool FAILS (vs
        PostToolUse for success)."""
        cwd = str(cwd)
        hooks = self._hooks.get(HookEvent.POST_TOOL_USE_FAILURE, [])
        result = HookResult()
        contexts: list[str] = []

        for hook in hooks:
            if not _matches_pattern(tool_name, hook.match):
                continue

            hook_result = await self._execute_hook(
                hook,
                {
                    "hook_event_name": HookEvent.POST_TOOL_USE_FAILURE.value,
                    "session_id": session_id,
                    "transcript_path": transcript_path,
                    "cwd": cwd,
                    "permission_mode": permission_mode,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_use_id": tool_use_id,
                    "error": error,
                    "is_interrupt": is_interrupt,
                },
            )

            if hook_result.blocking_error:
                result.blocking_error = hook_result.blocking_error
            if hook_result.prevent_continuation:
                result.prevent_continuation = True
            if hook_result.additional_context:
                contexts.append(hook_result.additional_context)

        if contexts:
            result.additional_context = "\n".join(contexts)
        return result

    async def run_stop_hooks(
        self,
        last_assistant_message: str = "",
        session_id: str = "",
        cwd: Path | str = "",
        transcript_path: str = "",
        permission_mode: str | None = None,
        stop_hook_active: bool = False,
    ) -> HookResult:
        """
        Run Stop hooks — fired when the main agent turn finishes (model stopped
        without more tool calls). A blocking Stop hook forces the agent to keep
        going.
        stdin mirrors StopHookInput.
        """
        cwd = str(cwd)
        hooks = self._hooks.get(HookEvent.STOP, [])
        result = HookResult()
        contexts: list[str] = []

        for hook in hooks:
            hook_result = await self._execute_hook(
                hook,
                {
                    "hook_event_name": HookEvent.STOP.value,
                    "session_id": session_id,
                    "transcript_path": transcript_path,
                    "cwd": cwd,
                    "permission_mode": permission_mode,
                    "stop_hook_active": stop_hook_active,
                    "last_assistant_message": last_assistant_message,
                },
            )

            if hook_result.prevent_continuation:
                result.prevent_continuation = True
            if hook_result.blocking_error:
                result.blocking_error = hook_result.blocking_error
            if hook_result.additional_context:
                contexts.append(hook_result.additional_context)

        if contexts:
            result.additional_context = "\n".join(contexts)
        return result

    async def run_event_hooks(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> HookResult:
        """
        Run hooks for any event type (SessionStart, SessionEnd, etc).
        fires hooks for all 27 event types at their lifecycle points.
        """
        hooks = self._hooks.get(event, [])
        if not hooks:
            return HookResult()

        data = data or {}
        ev = getattr(event, "value", event)

        field = _EVENT_MATCH_QUERY_FIELD.get(ev)
        match_query = None
        if field:
            mq = data.get(field)
            if ev == "FileChanged" and mq:
                from pathlib import Path as _P

                mq = _P(str(mq)).name
            match_query = mq if mq else None

        timeout_override = _session_end_timeout_sec() if ev == "SessionEnd" else None

        result = HookResult()
        contexts: list[str] = []
        for hook in hooks:
            if (
                match_query is not None
                and hook.match
                and not _matches_pattern(match_query, hook.match)
            ):
                continue
            hook_data = {
                "hook_event_name": ev,
                **self._base_context,
                **data,
            }
            hook_result = await self._execute_hook(hook, hook_data, timeout_override)

            if hook_result.blocking_error and not result.blocking_error:
                result.blocking_error = hook_result.blocking_error
            if hook_result.additional_context:
                contexts.append(hook_result.additional_context)
            if hook_result.prevent_continuation:
                result.prevent_continuation = True

        if contexts:
            result.additional_context = "\n".join(contexts)
        return result

    async def _execute_hook(
        self, hook: HookCommand, env_data: dict, timeout_override: float | None = None
    ) -> HookResult:
        """Execute a single hook (command or http)."""
        if hook.type == "command":
            return await self._execute_command_hook(hook, env_data, timeout_override)
        elif hook.type == "http":
            return await self._execute_http_hook(hook, env_data, timeout_override)
        return HookResult()

    async def _execute_command_hook(
        self, hook: HookCommand, env_data: dict, timeout_override: float | None = None
    ) -> HookResult:
        """
        Execute a shell command hook.

          - Env: `AGENT_PROJECT_DIR` (always), filtered subprocess env.
          - stdin: hook input as JSON (base fields + event-specific fields).
            One line, UTF-8.
          - stdout: optional JSON response with the hook protocol.
        """
        env = _filter_env_secrets(os.environ)
        cwd_val = env_data.get("cwd")
        if cwd_val:
            env["AGENT_PROJECT_DIR"] = str(cwd_val)

        json_input = json.dumps(env_data, default=str) + "\n"

        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                _timeout = (
                    timeout_override
                    if timeout_override is not None
                    else (hook.timeout or DEFAULT_HOOK_TIMEOUT)
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=json_input.encode("utf-8")),
                    timeout=_timeout,
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, PermissionError):
                    pass
                raise
            output = stdout.decode().strip()
            stderr_s = stderr.decode().strip()
            rc = proc.returncode

            if output.startswith("{"):
                try:
                    return _parse_hook_json(json.loads(output))
                except json.JSONDecodeError:
                    pass

            if rc == 2:
                return HookResult(
                    outcome="blocking",
                    blocking_error=f"[{hook.command}]: {stderr_s or 'No stderr output'}",
                )
            if rc not in (0, None):
                logger.warning(f"Hook exited {rc}: {hook.command}: {stderr_s[:200]}")
                return HookResult(outcome="error")

            return HookResult(
                outcome="success",
                additional_context=output if output else None,
            )

        except asyncio.TimeoutError:
            msg = f"Hook timed out after {hook.timeout}s: {hook.command}"
            logger.warning(msg)
            if hook.block:
                return HookResult(outcome="blocking", blocking_error=msg)
            return HookResult(outcome="error")

        except Exception as e:
            logger.warning(f"Hook error: {e}")
            if hook.block:
                return HookResult(outcome="blocking", blocking_error=str(e))
            return HookResult(outcome="error")

    async def _execute_http_hook(
        self, hook: HookCommand, env_data: dict, timeout_override: float | None = None
    ) -> HookResult:
        """Execute an HTTP webhook hook."""
        try:
            import httpx

            _timeout = (
                timeout_override
                if timeout_override is not None
                else (hook.timeout or DEFAULT_HOOK_TIMEOUT)
            )
            async with httpx.AsyncClient(timeout=_timeout) as client:
                response = await client.post(hook.url, json=env_data)
                if response.status_code == 200:
                    try:
                        return _parse_hook_json(response.json())
                    except Exception:
                        pass
                return HookResult()
        except Exception as e:
            logger.warning(f"HTTP hook error: {e}")
            return HookResult(outcome="error")
