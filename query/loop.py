"""
Query Loop: The heart of the agent — async generator state machine.

Source: Claude Code src/query.ts (~1700 lines)

Architecture:
  while True:
    1. COMPACT (microcompact + autocompact)
    2. STREAM MODEL (API call, yield events)
    3. PERMISSION CHECK (per-tool, before execution)
    4. PRE-TOOL HOOKS (can block/modify)
    5. EXECUTE TOOLS (parallel for reads, serial for writes)
    6. POST-TOOL HOOKS (can stop continuation)
    7. RECOVER (if errors)
    8. CONTINUE or STOP

Tracks WHY it continued via transition.reason.
Circuit breakers prevent infinite retries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import AsyncGenerator

from agent.event import AgentEvent
from client.response import StreamEventType, ToolCall
from core_types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from hooks.executor import HookExecutor
from permissions.checker import PermissionChecker
from query.transitions import Continue
from services.compact.auto_compact import (
    AutoCompactTracking,
    calculate_token_warning_state,
)
from services.tools.orchestration import ToolCallRequest, run_tools

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3


@dataclass
class QueryState:
    """Mutable state carried between loop iterations. Claude Code: State in query.ts:204."""
    turn_count: int = 1
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    compact_tracking: AutoCompactTracking | None = None
    transition: Continue | None = None
    recent_tool_hashes: list[str] = field(default_factory=list)


def _hash_tool_call(name: str, args: dict) -> str:
    import hashlib
    import json
    key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


async def query_loop(
    session,
    config,
    permission_prompt_fn=None,
    abort_controller=None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    The main query loop — async generator state machine.
    Mirrors Claude Code's queryLoop() exactly.
    """
    state = QueryState()
    max_turns = config.max_turns

    # Build permission checker
    permission_context = ToolPermissionContext(
        mode=PermissionMode(config.permission_mode) if hasattr(config, 'permission_mode') and config.permission_mode else PermissionMode.DEFAULT,
        working_directories=(str(config.cwd),),
    )
    permission_checker = PermissionChecker(permission_context)

    # Build hook executor (from config if available)
    hook_executor = HookExecutor()

    while True:
        # ══════════════════════════════════════════════════════════
        # STEP 1: COMPACT PHASE (4 layers)
        # Claude Code order: snip → microcompact → context collapse → autocompact
        # ══════════════════════════════════════════════════════════

        # Layer 0: Snip compact — remove oldest message groups if way over
        from services.compact.snip_compact import snip_compact_if_needed
        context_window = config.model.context_window
        session.context_manager._messages, snip_freed = snip_compact_if_needed(
            session.context_manager._messages, context_window,
        )

        # Layer 1-3: microcompact + context collapse + autocompact (inside get_messages_with_compaction)
        messages = await session.context_manager.get_messages_with_compaction()

        # Layer 1.5: Context collapse (between microcompact and autocompact)
        from services.compact.context_collapse import apply_context_collapse
        session.context_manager._messages = apply_context_collapse(
            session.context_manager._messages, context_window,
        )

        # Apply tool result budget (Claude Code: MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200K)
        session.context_manager.apply_tool_result_budget()

        # Re-fetch messages after all compaction layers
        messages = session.context_manager.get_message()

        # ── BLOCKING LIMIT CHECK ──
        # Claude Code: synthetic 413 before API call if at blocking limit
        # Prevents wasted API calls when context is irrecoverably over limit
        context_window = config.model.context_window
        token_count = sum(
            len(m.get("content", "")) // 4 for m in messages  # rough estimate
        )
        warning_state = calculate_token_warning_state(token_count, context_window)

        if warning_state.is_at_blocking_limit:
            if not state.has_attempted_reactive_compact:
                logger.warning(f"At blocking limit ({token_count} tokens) — forcing compact")
                state.has_attempted_reactive_compact = True
                state.transition = Continue(reason="reactive_compact_retry")
                continue
            else:
                yield AgentEvent.agent_error(
                    "Context too large even after compaction. Use /clear to start fresh."
                )
                return

        # ══════════════════════════════════════════════════════════
        # STEP 2: MODEL STREAMING PHASE
        # Claude Code: StreamingToolExecutor starts tools DURING streaming
        # ══════════════════════════════════════════════════════════
        response_text = ""
        tool_calls: list[ToolCall] = []
        tool_schemas = session.tool_registry.get_schemas()
        api_error: str | None = None

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
                    # Handle retry reset — discard partial content from failed attempt
                    if error_msg.startswith("__RETRY_RESET__"):
                        response_text = ""
                        tool_calls = []
                        continue
                    api_error = error_msg
                    yield AgentEvent.agent_error(api_error)

        except Exception as e:
            error_str = str(e).lower()
            api_error = str(e)

            # Fallback model on 529/overloaded errors (Claude Code: FallbackTriggeredError)
            if ("529" in error_str or "overloaded" in error_str) and hasattr(config, '_fallback_attempted') and not config._fallback_attempted:
                logger.warning("Model overloaded — would switch to fallback model")
                # In a full implementation, this would switch to a fallback model
                # and retry the request. For now, log and continue.

            logger.exception("Model streaming error")
            yield AgentEvent.agent_error(f"Model error: {e}")

        # ══════════════════════════════════════════════════════════
        # STEP 3: ADD ASSISTANT MESSAGE TO CONTEXT + PERSIST
        # ══════════════════════════════════════════════════════════
        import json as _json
        tool_calls_data = [
            {
                "id": tc.call_id,
                "type": "function",
                "function": {"name": tc.name, "arguments": _json.dumps(tc.arguments, default=str)},
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

        # ══════════════════════════════════════════════════════════
        # STEP 4: CONTINUE DECISION
        # Claude Code line 1062: if (!needsFollowUp)
        # ══════════════════════════════════════════════════════════
        needs_follow_up = len(tool_calls) > 0

        if not needs_follow_up:
            # ── RECOVERY: Prompt too long (413) ──
            if api_error and "prompt" in api_error.lower() and "long" in api_error.lower():
                if not state.has_attempted_reactive_compact:
                    logger.info("Prompt too long — attempting reactive compact")
                    state.has_attempted_reactive_compact = True

                    # Run reactive compact (Claude Code Layer 4)
                    from services.compact.reactive_compact import try_reactive_compact
                    if session.context_manager._compact_fn:
                        compacted = await try_reactive_compact(
                            messages=session.context_manager._messages,
                            compact_fn=session.context_manager._compact_fn,
                            system_prompt=session.context_manager.system_prompt,
                        )
                        if compacted:
                            session.context_manager._messages = compacted
                            logger.info("Reactive compact succeeded — retrying")

                    state.transition = Continue(reason="reactive_compact_retry")
                    continue
                else:
                    yield AgentEvent.agent_error("Context too large even after reactive compaction")
                    return

            # ── RECOVERY: Max output tokens ──
            # Claude Code: isWithheldMaxOutputTokens check + recovery loop
            if api_error and "max" in api_error.lower() and "token" in api_error.lower():
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
                    state.transition = Continue(reason="max_output_tokens_retry")
                    continue
                else:
                    yield AgentEvent.agent_error(
                        f"Max output tokens exceeded after {MAX_OUTPUT_TOKENS_RECOVERY_LIMIT} retries"
                    )
                    return

            return

        # ══════════════════════════════════════════════════════════
        # STEP 5: TOOL EXECUTION WITH PERMISSIONS + HOOKS
        # Claude Code: runToolUse() lifecycle:
        #   validate → permission → pre-hooks → execute → post-hooks
        # ══════════════════════════════════════════════════════════
        tool_requests = [
            ToolCallRequest(
                call_id=tc.call_id,
                name=tc.name,
                arguments=tc.arguments if isinstance(tc.arguments, dict) else {},
            )
            for tc in tool_calls
        ]

        # ── LOOP DETECTION ──
        for req in tool_requests:
            h = _hash_tool_call(req.name, req.arguments)
            state.recent_tool_hashes.append(h)

        if len(state.recent_tool_hashes) >= 10:
            recent = state.recent_tool_hashes[-10:]
            for h in set(recent):
                if recent.count(h) >= 3:
                    logger.warning("Loop detected: tool call repeated 3+ times")
                    yield AgentEvent.agent_error(
                        "Possible infinite loop detected — same tool called repeatedly with same arguments."
                    )
                    return

        if len(state.recent_tool_hashes) > 20:
            state.recent_tool_hashes = state.recent_tool_hashes[-20:]

        # Build tools dict
        tools_dict = {tool.name: tool for tool in session.tool_registry.get_tools()}

        # ── PERMISSION CHECK BEFORE EXECUTION (CRITICAL) ──
        # Must check permissions BEFORE running tools, not after
        from tools.base import ToolResult
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

            if perm_result.behavior == PermissionBehavior.DENY:
                denied = ToolResult.error_result(f"Permission denied: {perm_result.message}")
                yield AgentEvent.tool_call_start(call_id=req.call_id, name=req.name, arguments=req.arguments)
                yield AgentEvent.tool_call_complete(call_id=req.call_id, name=req.name, result=denied)
                session.context_manager.add_tool_result(tool_call_id=req.call_id, content=denied.to_model_output())
                continue

            if perm_result.behavior == PermissionBehavior.ASK:
                if permission_prompt_fn:
                    approved = await permission_prompt_fn(
                        req.name, perm_result.message,
                        tool.is_destructive(req.arguments) if tool else False,
                    )
                    if not approved:
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

        # ── PRE-TOOL HOOKS (BEFORE execution, not after) ──
        hook_approved = []
        for req in approved_requests:
            hook_result = await hook_executor.run_pre_tool_hooks(
                tool_name=req.name,
                tool_input=req.arguments,
                session_id=session.session_id,
                cwd=str(config.cwd),
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

            # ── PLAN MODE: Handle permission mode changes from tools ──
            # EnterPlanMode/ExitPlanMode set permission_mode via metadata
            if result.result.metadata.get("set_permission_mode"):
                new_mode = result.result.metadata["set_permission_mode"]
                config.permission_mode = new_mode
                logger.info(f"Permission mode changed to: {new_mode}")

            # ── POST-TOOL HOOKS ──
            post_hook = await hook_executor.run_post_tool_hooks(
                tool_name=result.name,
                tool_input=result.arguments,
                tool_output=tool_output,
                session_id=session.session_id,
                cwd=str(config.cwd),
            )
            if post_hook.prevent_continuation:
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

        # Check max turns (Claude Code line 1705)
        if state.turn_count > max_turns:
            yield AgentEvent.agent_error(f"Max turns ({max_turns}) reached")
            return

        state.transition = Continue(reason="tool_execution")
