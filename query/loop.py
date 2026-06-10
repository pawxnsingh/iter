"""
Query Loop: async generator state machine that processes one user message.

Mirrors Claude Code's src/query.ts. Each iteration is one LLM round-trip
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
    ToolPermissionContext,
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
    # Cumulative tokens spent across iterations of this user message.
    # Compared against config.task_budget_tokens; abort if over.
    task_tokens_used: int = 0
    # Stashed stream/API error. The continue-decision branch decides
    # whether recovery consumes it or the user needs to see it.
    pending_error: str | None = None
    # True once a Stop hook has already forced a continuation this turn. CC sets
    # stop_hook_active so a Stop hook can't trap the agent in an infinite loop.
    stop_hook_active: bool = False


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

    # Build permission checker
    permission_context = ToolPermissionContext(
        mode=PermissionMode(config.permission_mode) if hasattr(config, 'permission_mode') and config.permission_mode else PermissionMode.DEFAULT,
        working_directories=(str(config.cwd),),
        allow_rules=getattr(config, "allow_rules", {}) or {},
        deny_rules=getattr(config, "deny_rules", {}) or {},
        ask_rules=getattr(config, "ask_rules", {}) or {},
    )
    permission_checker = PermissionChecker(permission_context)

    # Use the hook executor passed from Agent (carries the snapshotted hooks
    # config); fall back to empty if invoked standalone.
    if hook_executor is None:
        hook_executor = HookExecutor()

    # Task budget — None means unlimited
    task_budget = getattr(config, "task_budget_tokens", None)
    
    while True:
        # ── TASK BUDGET CHECK ──
        # Claude Code: per-message budget tracker. Abort if cumulative
        # token spend exceeds the configured budget.
        if task_budget is not None and state.task_tokens_used >= task_budget:
            yield AgentEvent.agent_error(
                f"Task budget exhausted: spent {state.task_tokens_used:,} of "
                f"{task_budget:,} tokens. Abandoning further iterations."
            )
            return

        # ══════════════════════════════════════════════════════════
        # STEP 1: COMPACT PHASE
        # Full pipeline runs inside get_messages_with_compaction in the
        # order Claude Code uses (query.ts ~396-468):
        #   snip → microcompact → context_collapse → autocompact → tool_result_budget
        #
        # No synthetic-413 preempt here. Claude Code skips that block when
        # reactive compact is enabled (query.ts ~628-647): the synthetic
        # error returns before the API call, so reactive compact never sees
        # a real prompt-too-long to react to. We always have reactive
        # compact, so we let the real 413 flow into STEP 4 recovery.
        # ══════════════════════════════════════════════════════════
        messages = await session.context_manager.get_messages_with_compaction()

        # ══════════════════════════════════════════════════════════
        # STEP 2: MODEL STREAMING PHASE
        # ══════════════════════════════════════════════════════════
        response_text = ""
        tool_calls: list[ToolCall] = []
        tool_schemas = session.tool_registry.get_schemas()
        api_error: str | None = None
        turn_usage: TokenUsage | None = None

        session.context_manager.set_turn(state.turn_count)

        try:
            async for event in session.client.chat_completion(
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
                stream=True,
            ):
                # ── ABORT CHECK during streaming ──
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
                    # `__RETRY_RESET__` is our internal contract between
                    # LLMClient and the loop: when the client retries
                    # mid-stream (e.g. transient 5xx), it emits this marker
                    # so we discard the partial response we collected and
                    # keep listening from the start of the new attempt.
                    if error_msg.startswith("__RETRY_RESET__"):
                        response_text = ""
                        tool_calls = []
                        continue
                    # Error withholding: stash, do not yield yet. The
                    # continue-decision below decides whether recovery
                    # consumes this error or the user needs to see it.
                    api_error = error_msg

                # Capture token usage if the provider streamed it
                if event.usage is not None:
                    turn_usage = event.usage

        except Exception as e:
            # Stash, don't yield — recovery decides.
            api_error = str(e)
            logger.exception("Model streaming error")

        # Update task budget with this turn's usage (provider-agnostic).
        # Falls back to a rough estimate if the provider didn't report usage.
        if turn_usage is not None:
            state.task_tokens_used += turn_usage.total_tokens or (
                (turn_usage.prompt_tokens or 0) + (turn_usage.completion_tokens or 0)
            )
        elif response_text:
            state.task_tokens_used += estimate_token(response_text)

        # Stash the error for the recovery branch below.
        state.pending_error = api_error

        # ══════════════════════════════════════════════════════════
        # STEP 3: ADD ASSISTANT MESSAGE TO CONTEXT + PERSIST
        # ══════════════════════════════════════════════════════════
        tool_calls_data = [
            {
                "id": tc.call_id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, default=str)},
            }
            for tc in tool_calls
        ] if tool_calls else None


        # context_manager = in-memory message list. 
        # Next iteration's compaction phase uses this.
        session.context_manager.add_assistant_message(
            content=response_text,
            tool_calls=tool_calls_data,
        )
        # save_message = appends to JSONL on disk.
        session.save_message(
            role="assistant", content=response_text,
            tool_calls=tool_calls_data, turn=state.turn_count,
        )

        if response_text:
            yield AgentEvent.text_complete(content=response_text)

        # ── POST-SAMPLING HOOK ──
        # Fires after streaming completes but BEFORE tools execute. Lets
        # observers see the full assistant message + intended tool calls.
        # Hook can return additional_context to inject into next turn.
        
        # DIDN'T GOT THAT, WILL HAVE UNDERSTAND THAT AGAIN
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

        # ══════════════════════════════════════════════════════════
        # STEP 4: CONTINUE DECISION
        # Claude Code line 1062: if (!needsFollowUp)
        # ══════════════════════════════════════════════════════════
        needs_follow_up = len(tool_calls) > 0
        
                
        # The fundamental rule:
        # - Model emitted tool calls → continue (fall through to STEP 5, then loop again)
        # - Model emitted only text → stop (with recovery branches first)


        if not needs_follow_up:
            error_kind = classify_error(state.pending_error) if state.pending_error else ErrorKind.OTHER

            # ── RECOVERY: Prompt too long (413) ──
            if error_kind == ErrorKind.PROMPT_TOO_LONG:
                if not state.has_attempted_reactive_compact:
                    logger.info("Prompt too long — attempting reactive compact")
                    state.has_attempted_reactive_compact = True

                    # Reactive compact: emergency LLM-driven summarisation
                    # triggered when the API returns prompt-too-long mid-call.
                    if session.context_manager._compact_fn:
                        compacted = await try_reactive_compact(
                            messages=session.context_manager._messages,
                            compact_fn=session.context_manager._compact_fn,
                            system_prompt=session.context_manager.system_prompt,
                        )
                        if compacted:
                            session.context_manager._messages = compacted
                            logger.info("Reactive compact succeeded — retrying")

                    # Recovery is in flight — withhold the error from user.
                    state.pending_error = None
                    continue
                else:
                    yield AgentEvent.agent_error("Context too large even after reactive compaction")
                    return

            # ── RECOVERY: Max output tokens ──
            # Claude Code: isWithheldMaxOutputTokens check + recovery loop
            if error_kind == ErrorKind.MAX_OUTPUT_TOKENS:
                if state.max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                    state.max_output_tokens_recovery_count += 1
                    logger.info(
                        f"Max output tokens hit — recovery attempt "
                        f"{state.max_output_tokens_recovery_count}/{MAX_OUTPUT_TOKENS_RECOVERY_LIMIT}"
                    )
                    # Inject recovery message (Claude Code pattern)
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

            # No recovery applicable — surface the error to the user (if any).
            if state.pending_error:
                yield AgentEvent.agent_error(state.pending_error)
                state.pending_error = None
                return

            # ── STOP HOOK ──
            # Clean stop: the model finished with text only. Fire Stop hooks
            # (CC: executeStopHooks). A blocking Stop hook means "don't stop yet"
            # — its message is fed back to the model and the loop continues.
            # stop_hook_active guards against an infinite block loop.
            if not state.stop_hook_active:
                stop_hook = await hook_executor.run_stop_hooks(
                    last_assistant_message=response_text,
                    session_id=session.session_id,
                    cwd=str(config.cwd),
                    transcript_path=getattr(session, "transcript_path", "") or "",
                    permission_mode=str(getattr(config, "permission_mode", "") or ""),
                    stop_hook_active=state.stop_hook_active,
                )
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

        # ══════════════════════════════════════════════════════════
        # STEP 5: TOOL EXECUTION WITH PERMISSIONS + HOOKS
        # Claude Code: runToolUse() lifecycle:
        #   validate → permission → pre-hooks → execute → post-hooks
        # ══════════════════════════════════════════════════════════
        
        # Convert ToolCall (the streaming representation) to ToolCallRequest 
        # (what run_tools accepts). Build a name→Tool lookup.
        
        tool_requests = [
            ToolCallRequest(
                call_id=tc.call_id,
                name=tc.name,
                arguments=tc.arguments if isinstance(tc.arguments, dict) else {},
            )
            for tc in tool_calls
        ]

        # Build tools dict
        tools_dict = {tool.name: tool for tool in session.tool_registry.get_tools()}

        # ── PERMISSION CHECK BEFORE EXECUTION (CRITICAL) ──
        # Must check permissions BEFORE running tools, not after
        approved_requests = []

        for req in tool_requests:
            tool = tools_dict.get(req.name)
            if not tool:
                # Unknown tools get immediate error — never bypass permissions
                unknown_err = ToolResult.error_result(f"Unknown tool: {req.name}")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=unknown_err)
                session.context_manager.add_tool_result(tool_call_id=req.call_id, content=unknown_err.to_model_output())
                continue

            perm_result = await permission_checker.check(tool, req.arguments)

            _perm_common = dict(
                session_id=session.session_id,
                cwd=str(config.cwd),
                permission_mode=str(getattr(config, "permission_mode", "") or ""),
            )

            if perm_result.behavior == PermissionBehavior.DENY:
                # PermissionDenied hook (CC: executePermissionDeniedHooks).
                await hook_executor.run_event_hooks(
                    event=HookEvent.PERMISSION_DENIED.value,
                    data={"tool_name": req.name, "tool_input": req.arguments,
                          "tool_use_id": req.call_id, "reason": perm_result.message,
                          **_perm_common},
                )
                denied = ToolResult.error_result(f"Permission denied: {perm_result.message}")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=denied)
                session.context_manager.add_tool_result(tool_call_id=req.call_id, content=denied.to_model_output())
                continue

            if perm_result.behavior == PermissionBehavior.ASK:
                # PermissionRequest hook (CC: executePermissionRequestHooks) +
                # Notification hook (CC fires a Notification when prompting).
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
                if permission_prompt_fn:
                    approved = await permission_prompt_fn(
                        req.name, perm_result.message,
                        tool.is_destructive(req.arguments) if tool else False,
                    )
                    if not approved:
                        # User declined → PermissionDenied (reason: user).
                        await hook_executor.run_event_hooks(
                            event=HookEvent.PERMISSION_DENIED.value,
                            data={"tool_name": req.name, "tool_input": req.arguments,
                                  "tool_use_id": req.call_id, "reason": "denied by user",
                                  **_perm_common},
                        )
                        denied = ToolResult.error_result("Permission denied by user")
                        yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                        yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=denied)
                        session.context_manager.add_tool_result(tool_call_id=req.call_id, content=denied.to_model_output())
                        continue
                else:
                    # No prompt function — default DENY (not silent allow)
                    denied = ToolResult.error_result("Permission required but no approval mechanism available")
                    yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                    yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=denied)
                    session.context_manager.add_tool_result(tool_call_id=req.call_id, content=denied.to_model_output())
                    continue

            approved_requests.append(req)

        # ── EXECUTE ONLY APPROVED TOOLS ──
        should_stop = False

        # Abort check before running tools (CC: signal.aborted before runToolUse).
        # Without this, a Ctrl+C after the stream finishes but before/between
        # tool execution wouldn't be honored until the next round-trip.
        if abort_controller and abort_controller.aborted:
            yield AgentEvent.agent_error("Interrupted by user")
            return

        # ── PRE-TOOL HOOKS (BEFORE execution, not after) ──
        hook_approved = []
        for req in approved_requests:
            hook_result = await hook_executor.run_pre_tool_hooks(
                tool_name=req.name,
                tool_input=req.arguments,
                session_id=session.session_id,
                cwd=str(config.cwd),
                tool_use_id=req.call_id,
                transcript_path=getattr(session, "transcript_path", "") or "",
                permission_mode=str(getattr(config, "permission_mode", "") or ""),
            )
            if hook_result.blocking_error:
                blocked_result = ToolResult.error_result(f"Blocked by hook: {hook_result.blocking_error}")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=blocked_result)
                session.context_manager.add_tool_result(
                    tool_call_id=req.call_id,
                    content=blocked_result.to_model_output(),
                )
                continue
            # Apply updated input from hook if provided
            if hook_result.updated_input:
                req.arguments = hook_result.updated_input
            hook_approved.append(req)

        async for result in run_tools(hook_approved, tools_dict, config.cwd):
            # ── YIELD EVENTS ──
            yield AgentEvent.tool_call_start(
                call_id=result.call_id, name=result.name, arguments=result.arguments,
            )
            yield AgentEvent.tool_call_complete(
                call_id=result.call_id, name=result.name, result=result.result,
            )

            # ── ADD TO CONTEXT + PERSIST ──
            tool_output = result.result.to_model_output()
            session.context_manager.add_tool_result(
                tool_call_id=result.call_id, content=tool_output,
            )
            session.save_message(
                role="tool", content=tool_output,
                tool_call_id=result.call_id, tool_name=result.name,
                turn=state.turn_count,
            )

            # Image attachments (CC parity: FileRead image blocks). Tool messages
            # can't carry images in the OpenAI shape, so surface them as a
            # follow-up user message the model can actually see.
            imgs = getattr(result.result, "images", None)
            if imgs:
                session.context_manager.add_image_message(
                    imgs, content=f"[image from {result.name}]"
                )

            # ── PLAN MODE: Handle permission mode changes from tools ──
            # EnterPlanMode/ExitPlanMode set permission_mode via metadata.
            # ToolPermissionContext is frozen, so rebuild the checker — just
            # mutating config.permission_mode would leave the live checker on
            # the old mode and the transition would not actually gate tools.
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

            # ── POST-TOOL HOOKS ──
            # CC fires PostToolUse on success and PostToolUseFailure on failure
            # (separate event names, separate hook configs). Route by result.
            common = dict(
                tool_name=result.name,
                tool_input=result.arguments,
                session_id=session.session_id,
                cwd=str(config.cwd),
                tool_use_id=result.call_id,
                transcript_path=getattr(session, "transcript_path", "") or "",
                permission_mode=str(getattr(config, "permission_mode", "") or ""),
            )
            if result.result.success:
                post_hook = await hook_executor.run_post_tool_hooks(
                    tool_output=tool_output, **common,
                )
            else:
                post_hook = await hook_executor.run_post_tool_failure_hooks(
                    error=tool_output, **common,
                )
            # Stop hooks blocking-error: a post-tool hook can inject a
            # blocking_error to force the agent to retry with feedback,
            # rather than just preventing continuation. The error becomes
            # a synthetic user message for the next iteration.
            if post_hook.blocking_error:
                logger.info(
                    f"Post-tool hook blocking on {result.name}: {post_hook.blocking_error}"
                )
                session.context_manager.add_user_message(
                    f"[hook feedback] {post_hook.blocking_error}"
                )
                # Don't set should_stop — fall through, loop continues.
            elif post_hook.prevent_continuation:
                should_stop = True

        if should_stop:
            return

        # ══════════════════════════════════════════════════════════
        # STEP 6: CONTINUE LOOP
        # ══════════════════════════════════════════════════════════
        state.turn_count += 1
        session.increment_turn()

        # Reset recovery state on new turn (Claude Code line 1720-1721)
        state.max_output_tokens_recovery_count = 0
        state.has_attempted_reactive_compact = False
        # The model did real work (tool calls) this iteration, so a future
        # natural stop should be able to fire Stop hooks again.
        state.stop_hook_active = False

        # Check max turns (Claude Code line 1705)
        if state.turn_count > max_turns:
            yield AgentEvent.agent_error(f"Max turns ({max_turns}) reached")
            return
