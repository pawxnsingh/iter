"""
Query Loop: async generator state machine that processes one user message.

plus tool execution. The model votes to continue by emitting tool calls
and votes to stop by responding with just text.

Per iteration:
    task budget check → compact pipeline → stream model →
    persist + POST_SAMPLING → continue decision (with recovery) →
    tool permission gate → pre-tool hooks → run tools → post-tool hooks →
    increment turn
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import AsyncGenerator

from agent.event import AgentEvent
from agent.session import Session
from client.llm_client import ErrorKind, classify_error
from client.response import StreamEventType, ToolCall, TokenUsage
from config.config import Config
from core_types.hooks import HookEvent
from core_types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    ToolPermissionContext,
    permission_mode_external,
)
from hooks.executor import HookExecutor
from permissions.checker import PermissionChecker
from services.compact.reactive_compact import try_reactive_compact
from services.tools.orchestration import ToolCallRequest, run_tools
from tools.base import ToolResult
from utils.text import estimate_token

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3


@dataclass
class QueryState:
    """Mutable state carried between loop iterations."""
    turn_count: int = 1
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    task_tokens_used: int = 0
    pending_error: str | None = None
    stop_hook_active: bool = False


def _persist_tool_result(session, call_id: str, content: str, name: str, turn: int) -> None:
    """Add a tool result to context AND persist it to the JSONL transcript.

    Both are required: writes a tool_result for EVERY tool_use — including
    denied/error/hook-blocked ones — so resume never sees an assistant tool_call
    without its matching result (an orphan that 400s the next API request).
    """
    session.context_manager.add_tool_result(tool_call_id=call_id, content=content)
    session.save_message(
        role="tool", content=content, tool_call_id=call_id,
        tool_name=name, turn=turn,
    )


async def query_loop(
    session: Session,
    config: Config,
    permission_prompt_fn=None,
    abort_controller=None,
    hook_executor: HookExecutor | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    The main query loop — async generator state machine.

    `hook_executor` should be the snapshotted instance the Agent built at
    session start (so PRE_TOOL_USE / POST_TOOL_USE actually fire). If the
    caller passes None, we fall back to an empty no-op executor so the loop
    is testable in isolation.

    query_loop is an async generator state machine that turns 
    one user message into a stream of AgentEvents by running
    multiple round-trips with the LLM until the model stops asking for tools. 
    Each iteration of the outer while True is one LLM round-trip plus its tool execution.

    """
    state = QueryState()
    max_turns = config.max_turns

    permission_context = ToolPermissionContext(
        mode=PermissionMode(config.permission_mode) if hasattr(config, 'permission_mode') and config.permission_mode else PermissionMode.DEFAULT,
        working_directories=(str(config.cwd),),
        allow_rules=getattr(config, "allow_rules", {}) or {},
        deny_rules=getattr(config, "deny_rules", {}) or {},
        ask_rules=getattr(config, "ask_rules", {}) or {},
    )
    permission_checker = PermissionChecker(permission_context)

    if hook_executor is None:
        hook_executor = HookExecutor()

    task_budget = getattr(config, "task_budget_tokens", None)

    while True:
        if task_budget is not None and state.task_tokens_used >= task_budget:
            yield AgentEvent.agent_error(
                f"Task budget exhausted: spent {state.task_tokens_used:,} of "
                f"{task_budget:,} tokens. Abandoning further iterations."
            )
            return

        messages = await session.context_manager.get_messages_with_compaction()

        response_text = ""
        tool_calls: list[ToolCall] = []
        tool_schemas = session.tool_registry.get_schemas()
        api_error: str | None = None
        turn_usage: TokenUsage | None = None
        finish_reason: str | None = None

        session.context_manager.set_turn(state.turn_count)

        try:
            async for event in session.client.chat_completion(
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
                stream=True,
            ):
                if abort_controller and abort_controller.aborted:
                    yield AgentEvent.agent_error("Interrupted by user")
                    return

                if event.type == StreamEventType.TEXT_DELTA:
                    if event.text_delta:
                        response_text += event.text_delta.content
                        yield AgentEvent.text_delta(event.text_delta.content)

                elif event.type == StreamEventType.TOOL_CALL_COMPLETED:
                    if event.tool_call:
                        tool_calls.append(event.tool_call)

                elif event.type == StreamEventType.ERROR:
                    error_msg = event.error or "Unknown API error"
                    if error_msg.startswith("__RETRY_RESET__"):
                        response_text = ""
                        tool_calls = []
                        continue
                    api_error = error_msg

                if event.usage is not None:
                    turn_usage = event.usage

                if event.finish_reason:
                    finish_reason = event.finish_reason

        except Exception as e:
            api_error = str(e)
            logger.exception("Model streaming error")

        if (
            api_error is None
            and not tool_calls
            and finish_reason in ("length", "max_tokens")
        ):
            api_error = "max_output_tokens: response truncated at the output token limit"

        if turn_usage is not None:
            state.task_tokens_used += turn_usage.total_tokens or (
                (turn_usage.prompt_tokens or 0) + (turn_usage.completion_tokens or 0)
            )
        elif response_text:
            state.task_tokens_used += estimate_token(response_text)

        state.pending_error = api_error

        tool_calls_data = [
            {
                "id": tc.call_id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, default=str)},
            }
            for tc in tool_calls
        ] if tool_calls else None


        session.context_manager.add_assistant_message(
            content=response_text,
            tool_calls=tool_calls_data,
        )
        session.save_message(
            role="assistant", content=response_text,
            tool_calls=tool_calls_data, turn=state.turn_count,
        )

        if response_text:
            yield AgentEvent.text_complete(content=response_text)


        post_sampling = await hook_executor.run_event_hooks(
            event=HookEvent.POST_SAMPLING,
            data={
                "session_id": session.session_id,
                "assistant_text": response_text,
                "tool_calls": [
                    {"id": tc.call_id, "name": tc.name, "arguments": tc.arguments}
                    for tc in tool_calls
                ],
                "turn": state.turn_count,
            },
        )

        if post_sampling.additional_context:
            session.context_manager.add_user_message(
                f"[post-sampling hook context]\n{post_sampling.additional_context}"
            )

        needs_follow_up = len(tool_calls) > 0


        if not needs_follow_up:
            error_kind = classify_error(state.pending_error) if state.pending_error else ErrorKind.OTHER

            if error_kind == ErrorKind.PROMPT_TOO_LONG:
                if not state.has_attempted_reactive_compact:
                    logger.info("Prompt too long — attempting reactive compact")
                    state.has_attempted_reactive_compact = True

                    if session.context_manager._compact_fn:
                        compacted = await try_reactive_compact(
                            messages=session.context_manager._messages,
                            compact_fn=session.context_manager._compact_fn,
                            system_prompt=session.context_manager.system_prompt,
                            transcript_path=getattr(session, "transcript_path", "") or None,
                        )
                        if compacted:
                            session.context_manager._messages = compacted
                            logger.info("Reactive compact succeeded — retrying")

                    state.pending_error = None
                    continue
                else:
                    yield AgentEvent.agent_error("Context too large even after reactive compaction")
                    return

            if error_kind == ErrorKind.MAX_OUTPUT_TOKENS:
                if state.max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                    state.max_output_tokens_recovery_count += 1
                    logger.info(
                        f"Max output tokens hit — recovery attempt "
                        f"{state.max_output_tokens_recovery_count}/{MAX_OUTPUT_TOKENS_RECOVERY_LIMIT}"
                    )
                    session.context_manager.add_user_message(
                        "Output token limit hit. Resume directly — no apology, no recap of what "
                        "you were doing. Pick up mid-thought if that is where the cut happened. "
                        "Break remaining work into smaller pieces."
                    )
                    state.pending_error = None
                    continue
                else:
                    yield AgentEvent.agent_error(
                        f"Max output tokens exceeded after {MAX_OUTPUT_TOKENS_RECOVERY_LIMIT} retries"
                    )
                    return

            if state.pending_error:
                yield AgentEvent.agent_error(state.pending_error)
                state.pending_error = None
                return

            stop_hook = await hook_executor.run_stop_hooks(
                last_assistant_message=response_text,
                session_id=session.session_id,
                cwd=str(config.cwd),
                transcript_path=getattr(session, "transcript_path", "") or "",
                permission_mode=permission_mode_external(getattr(config, "permission_mode", None)),
                stop_hook_active=state.stop_hook_active,
            )
            if stop_hook.prevent_continuation:
                return
            if stop_hook.blocking_error:
                state.stop_hook_active = True
                session.context_manager.add_user_message(
                    f"[stop hook] {stop_hook.blocking_error}"
                )
                state.turn_count += 1
                if state.turn_count > max_turns:
                    yield AgentEvent.agent_error(f"Max turns ({max_turns}) reached")
                    return
                continue
            return


        tool_requests = [
            ToolCallRequest(
                call_id=tc.call_id,
                name=tc.name,
                arguments=tc.arguments if isinstance(tc.arguments, dict) else {},
            )
            for tc in tool_calls
        ]

        tools_dict = {tool.name: tool for tool in session.tool_registry.get_tools()}


        if abort_controller and abort_controller.aborted:
            for req in tool_requests:
                interrupted = ToolResult.error_result(
                    "Tool execution interrupted by user"
                )
                _persist_tool_result(
                    session, req.call_id, interrupted.to_model_output(),
                    req.name, state.turn_count,
                )
            yield AgentEvent.agent_error("Interrupted by user")
            return

        should_stop = False
        hook_approved = []

        for req in tool_requests:
            if abort_controller and abort_controller.aborted:
                interrupted = ToolResult.error_result("Tool execution interrupted by user")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=interrupted)
                _persist_tool_result(session, req.call_id, interrupted.to_model_output(), req.name, state.turn_count)
                continue

            tool = tools_dict.get(req.name)
            if not tool:
                unknown_err = ToolResult.error_result(f"Unknown tool: {req.name}")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=unknown_err)
                _persist_tool_result(session, req.call_id, unknown_err.to_model_output(), req.name, state.turn_count)
                continue

            _perm_common = dict(
                session_id=session.session_id,
                cwd=str(config.cwd),
                permission_mode=permission_mode_external(getattr(config, "permission_mode", None)),
            )

            pre_hook = await hook_executor.run_pre_tool_hooks(
                tool_name=req.name,
                tool_input=req.arguments,
                session_id=session.session_id,
                cwd=str(config.cwd),
                tool_use_id=req.call_id,
                transcript_path=getattr(session, "transcript_path", "") or "",
                permission_mode=permission_mode_external(getattr(config, "permission_mode", None)),
            )
            if pre_hook.updated_input:
                req.arguments = pre_hook.updated_input

            if pre_hook.blocking_error:
                blocked = ToolResult.error_result(f"Blocked by hook: {pre_hook.blocking_error}")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=blocked)
                _persist_tool_result(session, req.call_id, blocked.to_model_output(), req.name, state.turn_count)
                continue

            if pre_hook.permission_decision == "allow":
                perm_result = permission_checker.check_rules_only(tool, req.arguments) \
                    or PermissionResult.allow(reason="hook:allow")
            elif pre_hook.permission_decision == "deny":
                perm_result = PermissionResult.deny(
                    pre_hook.reason or "Denied by PreToolUse hook", reason="hook:deny"
                )
            elif pre_hook.permission_decision == "ask" or pre_hook.requires_ask:
                perm_result = PermissionResult.ask(
                    pre_hook.reason or "A hook requested confirmation.", reason="hook:ask"
                )
            else:
                perm_result = await permission_checker.check(tool, req.arguments)

            if perm_result.behavior == PermissionBehavior.DENY:
                await hook_executor.run_event_hooks(
                    event=HookEvent.PERMISSION_DENIED.value,
                    data={"tool_name": req.name, "tool_input": req.arguments,
                          "tool_use_id": req.call_id, "reason": perm_result.message,
                          **_perm_common},
                )
                denied = ToolResult.error_result(f"Permission denied: {perm_result.message}")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=denied)
                _persist_tool_result(session, req.call_id, denied.to_model_output(), req.name, state.turn_count)
                continue

            if perm_result.behavior == PermissionBehavior.ASK:
                await hook_executor.run_event_hooks(
                    event=HookEvent.PERMISSION_REQUEST.value,
                    data={"tool_name": req.name, "tool_input": req.arguments,
                          "permission_suggestions": None, **_perm_common},
                )
                await hook_executor.run_event_hooks(
                    event=HookEvent.NOTIFICATION.value,
                    data={"message": perm_result.message, "title": "Permission required",
                          "notification_type": "permission_request", **_perm_common},
                )
                approved = False
                if permission_prompt_fn:
                    approved = await permission_prompt_fn(
                        req.name, perm_result.message, tool.is_destructive(req.arguments),
                    )
                if not approved:
                    reason = "denied by user" if permission_prompt_fn else "no approval mechanism available"
                    await hook_executor.run_event_hooks(
                        event=HookEvent.PERMISSION_DENIED.value,
                        data={"tool_name": req.name, "tool_input": req.arguments,
                              "tool_use_id": req.call_id, "reason": reason, **_perm_common},
                    )
                    msg = ("Permission denied by user" if permission_prompt_fn
                           else "Permission required but no approval mechanism available")
                    denied = ToolResult.error_result(msg)
                    yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                    yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=denied)
                    _persist_tool_result(session, req.call_id, denied.to_model_output(), req.name, state.turn_count)
                    continue

            hook_approved.append(req)

        _storage = getattr(session, "storage", None)
        session_dir = _storage.path.parent if _storage is not None else None

        async for result in run_tools(
            hook_approved, tools_dict, config.cwd, abort_controller, session_dir
        ):
            yield AgentEvent.tool_call_start(
                call_id=result.call_id, name=result.name, arguments=result.arguments,
            )
            yield AgentEvent.tool_call_complete(
                call_id=result.call_id, name=result.name, result=result.result,
            )

            tool_output = result.result.to_model_output()
            if not tool_output.strip():
                tool_output = f"({result.name} completed with no output)"
            session.context_manager.add_tool_result(
                tool_call_id=result.call_id, content=tool_output,
            )
            session.save_message(
                role="tool", content=tool_output,
                tool_call_id=result.call_id, tool_name=result.name,
                turn=state.turn_count,
            )

            imgs = getattr(result.result, "images", None)
            if imgs:
                session.context_manager.add_image_message(
                    imgs, content=f"[image from {result.name}]"
                )

            if result.result.metadata.get("set_permission_mode"):
                new_mode = result.result.metadata["set_permission_mode"]
                config.permission_mode = new_mode
                permission_context = ToolPermissionContext(
                    mode=PermissionMode(new_mode),
                    working_directories=permission_context.working_directories,
                    allow_rules=permission_context.allow_rules,
                    deny_rules=permission_context.deny_rules,
                    ask_rules=permission_context.ask_rules,
                )
                permission_checker = PermissionChecker(permission_context)
                logger.info(f"Permission mode changed to: {new_mode}")

            common = dict(
                tool_name=result.name,
                tool_input=result.arguments,
                session_id=session.session_id,
                cwd=str(config.cwd),
                tool_use_id=result.call_id,
                transcript_path=getattr(session, "transcript_path", "") or "",
                permission_mode=permission_mode_external(getattr(config, "permission_mode", None)),
            )
            if result.result.success:
                post_hook = await hook_executor.run_post_tool_hooks(
                    tool_output=tool_output, **common,
                )
            else:
                post_hook = await hook_executor.run_post_tool_failure_hooks(
                    error=tool_output, **common,
                )
            if post_hook.blocking_error:
                logger.info(
                    f"Post-tool hook blocking on {result.name}: {post_hook.blocking_error}"
                )
                session.context_manager.add_user_message(
                    f"[hook feedback] {post_hook.blocking_error}"
                )
            elif post_hook.prevent_continuation:
                should_stop = True

            if post_hook.additional_context:
                session.context_manager.add_user_message(
                    f"[hook context] {post_hook.additional_context}"
                )

        if should_stop:
            return

        state.turn_count += 1
        session.increment_turn()

        state.max_output_tokens_recovery_count = 0
        state.has_attempted_reactive_compact = False
        state.stop_hook_active = False

        if state.turn_count > max_turns:
            yield AgentEvent.agent_error(f"Max turns ({max_turns}) reached")
            return
