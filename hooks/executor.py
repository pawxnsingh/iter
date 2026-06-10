"""
Hook Executor: Pre/post tool use hooks from configuration.

Source: Claude Code src/utils/hooks.ts (6K+ lines)

Hooks are user-defined commands that run at lifecycle points.
They communicate back via JSON on stdout.

Hook output protocol (from Claude Code):
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
import fnmatch
import json
import logging
import os
from pathlib import Path
from typing import Any

from core_types.hooks import HookCommand, HookEvent, HookResult

logger = logging.getLogger(__name__)

# Claude Code default: 600 seconds for tool hooks, 1.5s for SessionEnd
DEFAULT_HOOK_TIMEOUT = 600

# Patterns to strip from environment before passing to hook subprocesses
_SECRET_PATTERNS = (
    "*API_KEY*", "*SECRET*", "*TOKEN*", "*PASSWORD*", "*PASSWD*",
    "*CREDENTIAL*", "*PRIVATE_KEY*", "*AUTH*", "AWS_*",
    "ANTHROPIC_*", "OPENAI_*", "AZURE_*",
)


def _filter_env_secrets(environ: dict[str, str]) -> dict[str, str]:
    """Remove secret-bearing env vars before passing to hook subprocesses."""
    filtered = {}
    for key, value in environ.items():
        upper_key = key.upper()
        if any(fnmatch.fnmatch(upper_key, pat) for pat in _SECRET_PATTERNS):
            continue
        filtered[key] = value
    return filtered


class HookExecutor:
    """
    Executes hooks defined in configuration.
    Claude Code: executePreToolUseHooks/executePostToolUseHooks in hooks.ts
    """

    def __init__(self, hooks_config: dict[str, list[HookCommand]] | None = None):
        # event_name -> list of hook commands
        self._hooks = hooks_config or {}

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
        cwd = str(cwd)
        """
        Run PreToolUse hooks. Can block execution or modify input.
        Claude Code: executePreToolUseHooks() — stdin payload mirrors
        PreToolUseHookInput = createBaseHookInput ∪ {hook_event_name,
        tool_name, tool_input, tool_use_id}.
        """
        hooks = self._hooks.get(HookEvent.PRE_TOOL_USE, [])
        result = HookResult()

        for hook in hooks:
            # Match filter
            if hook.match and not fnmatch.fnmatch(tool_name, hook.match):
                continue

            hook_result = await self._execute_hook(hook, {
                "hook_event_name": HookEvent.PRE_TOOL_USE.value,
                "session_id": session_id,
                "transcript_path": transcript_path,
                "cwd": cwd,
                "permission_mode": permission_mode,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": tool_use_id,
            })

            # Blocking error stops execution
            if hook_result.blocking_error:
                return hook_result

            # Updated input passes through
            if hook_result.updated_input:
                result.updated_input = hook_result.updated_input

            # Decision overrides
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
        cwd = str(cwd)
        """
        Run PostToolUse hooks. Can add context or stop continuation.
        Claude Code: executePostToolUseHooks() — stdin payload mirrors
        PostToolUseHookInput.
        """
        hooks = self._hooks.get(HookEvent.POST_TOOL_USE, [])
        result = HookResult()

        for hook in hooks:
            if hook.match and not fnmatch.fnmatch(tool_name, hook.match):
                continue

            hook_result = await self._execute_hook(hook, {
                "hook_event_name": HookEvent.POST_TOOL_USE.value,
                "session_id": session_id,
                "transcript_path": transcript_path,
                "cwd": cwd,
                "permission_mode": permission_mode,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": tool_use_id,
                "tool_response": tool_output[:10000],
            })

            if hook_result.prevent_continuation:
                result.prevent_continuation = True

            if hook_result.additional_context:
                result.additional_context = hook_result.additional_context

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
        """
        Run PostToolUseFailure hooks — fired when a tool FAILS (vs PostToolUse
        for success). Claude Code: executePostToolUseFailureHooks() in
        services/tools/toolHooks.ts. stdin mirrors PostToolUseFailureHookInput.
        """
        cwd = str(cwd)
        hooks = self._hooks.get(HookEvent.POST_TOOL_USE_FAILURE, [])
        result = HookResult()

        for hook in hooks:
            if hook.match and not fnmatch.fnmatch(tool_name, hook.match):
                continue

            hook_result = await self._execute_hook(hook, {
                "hook_event_name": HookEvent.POST_TOOL_USE_FAILURE.value,
                "session_id": session_id,
                "transcript_path": transcript_path,
                "cwd": cwd,
                "permission_mode": permission_mode,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": tool_use_id,
                "error": error[:10000],
                "is_interrupt": is_interrupt,
            })

            if hook_result.prevent_continuation:
                result.prevent_continuation = True
            if hook_result.additional_context:
                result.additional_context = hook_result.additional_context

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
        going (CC: executeStopHooks → blockingError becomes a user message).
        stdin mirrors StopHookInput.
        """
        cwd = str(cwd)
        hooks = self._hooks.get(HookEvent.STOP, [])
        result = HookResult()

        for hook in hooks:
            hook_result = await self._execute_hook(hook, {
                "hook_event_name": HookEvent.STOP.value,
                "session_id": session_id,
                "transcript_path": transcript_path,
                "cwd": cwd,
                "permission_mode": permission_mode,
                "stop_hook_active": stop_hook_active,
                "last_assistant_message": last_assistant_message,
            })

            # A blocking Stop hook means "don't stop" — its message is fed back
            # to the model so it keeps working.
            if hook_result.blocking_error:
                result.blocking_error = hook_result.blocking_error
            if hook_result.additional_context:
                result.additional_context = hook_result.additional_context

        return result

    async def run_event_hooks(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> HookResult:
        """
        Run hooks for any event type (SessionStart, SessionEnd, etc).
        Claude Code fires hooks for all 27 event types at their lifecycle points.
        """
        hooks = self._hooks.get(event, [])
        if not hooks:
            return HookResult()

        result = HookResult()
        for hook in hooks:
            hook_data = {
                "hook_event_name": event,
                **(data or {}),
            }
            hook_result = await self._execute_hook(hook, hook_data)

            if hook_result.blocking_error:
                return hook_result
            if hook_result.additional_context:
                result.additional_context = hook_result.additional_context
            if hook_result.prevent_continuation:
                result.prevent_continuation = True

        return result

    async def _execute_hook(self, hook: HookCommand, env_data: dict) -> HookResult:
        """Execute a single hook (command or http)."""
        if hook.type == "command":
            return await self._execute_command_hook(hook, env_data)
        elif hook.type == "http":
            return await self._execute_http_hook(hook, env_data)
        return HookResult()

    async def _execute_command_hook(self, hook: HookCommand, env_data: dict) -> HookResult:
        """
        Execute a shell command hook.

        Claude Code parity (utils/hooks.ts):
          - Env: `CLAUDE_PROJECT_DIR` (always), filtered subprocess env.
          - stdin: hook input as JSON (createBaseHookInput + event-specific
            fields). One line, UTF-8.
          - stdout: optional JSON response with the hook protocol.
        """
        env = _filter_env_secrets(os.environ)
        cwd_val = env_data.get("cwd")
        if cwd_val:
            env["CLAUDE_PROJECT_DIR"] = str(cwd_val)

        # Build the CC stdin payload. Pydantic-free dict → JSON line.
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
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=json_input.encode("utf-8")),
                    timeout=hook.timeout or DEFAULT_HOOK_TIMEOUT,
                )
            except asyncio.TimeoutError:
                # Kill the subprocess on timeout (don't leave orphans)
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, PermissionError):
                    pass
                raise  # Re-raise to hit the outer TimeoutError handler
            output = stdout.decode().strip()

            # Parse JSON output (Claude Code protocol)
            if output.startswith("{"):
                try:
                    data = json.loads(output)
                    return HookResult(
                        outcome="blocking" if data.get("blockingError") else "success",
                        blocking_error=data.get("blockingError"),
                        decision=data.get("decision"),
                        reason=data.get("reason"),
                        updated_input=data.get("updatedInput"),
                        system_message=data.get("systemMessage"),
                        additional_context=data.get("additionalContext"),
                        prevent_continuation=data.get("preventContinuation", False),
                    )
                except json.JSONDecodeError:
                    pass

            # Non-JSON: treat as additional context
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

    async def _execute_http_hook(self, hook: HookCommand, env_data: dict) -> HookResult:
        """Execute an HTTP webhook hook."""
        try:
            import httpx
            async with httpx.AsyncClient(
                timeout=hook.timeout or DEFAULT_HOOK_TIMEOUT
            ) as client:
                response = await client.post(hook.url, json=env_data)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        return HookResult(
                            decision=data.get("decision"),
                            reason=data.get("reason"),
                            updated_input=data.get("updatedInput"),
                            additional_context=data.get("additionalContext"),
                        )
                    except Exception:
                        pass
                return HookResult()
        except Exception as e:
            logger.warning(f"HTTP hook error: {e}")
            return HookResult(outcome="error")
