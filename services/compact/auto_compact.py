"""
Autocompact: Layer 3 of Claude Code's compaction system.

Source: src/services/compact/autoCompact.ts

When the conversation token count exceeds a threshold, summarize the
entire conversation via an API call and replace old messages with the summary.

Constants (exact from Claude Code autoCompact.ts):
  AUTOCOMPACT_BUFFER_TOKENS = 13,000       (line 62)
  MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20,000   (line 30)
  MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3 (line 70)

Threshold formula:
  threshold = (context_window - MAX_OUTPUT_TOKENS_FOR_SUMMARY) - AUTOCOMPACT_BUFFER_TOKENS
  For 200K window: 200K - 20K - 13K = 167K tokens
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from utils.text import count_tokens

logger = logging.getLogger(__name__)

# ── Claude Code exact constants (autoCompact.ts lines 28-70) ──────────
AUTOCOMPACT_BUFFER_TOKENS = 13_000
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
POST_COMPACT_KEEP_RECENT = 6


@dataclass
class AutoCompactTracking:
    """
    Tracks compaction state across query loop iterations.
    Claude Code: AutoCompactTrackingState in autoCompact.ts line 51.
    """
    compacted: bool = False
    turn_counter: int = 0
    consecutive_failures: int = 0
    turn_id: str = ""  # Unique ID per compact (for analytics)


@dataclass
class CompactionResult:
    """Result from a compaction operation."""
    messages: list
    summary: str
    tracking: AutoCompactTracking
    pre_compact_tokens: int
    post_compact_tokens: int


# ── Threshold calculation ──────────────────────────────────────────────

def get_effective_context_window(context_window: int) -> int:
    """
    Claude Code: contextWindow - min(maxOutputTokens, 20_000)
    We reserve 20K tokens for the model's response during compaction.
    """
    return context_window - MAX_OUTPUT_TOKENS_FOR_SUMMARY


def get_auto_compact_threshold(context_window: int) -> int:
    """
    The token count at which autocompact fires.
    Claude Code: getAutoCompactThreshold() in autoCompact.ts line 72.
    """
    return get_effective_context_window(context_window) - AUTOCOMPACT_BUFFER_TOKENS


def get_blocking_limit(context_window: int) -> int:
    """
    Hard blocking limit. If tokens exceed this, generate synthetic 413 BEFORE calling API.
    Claude Code: blockingLimit = actualContextWindow - MANUAL_COMPACT_BUFFER_TOKENS (3K)
    Overridable via BLOCKING_LIMIT_OVERRIDE env var.
    """
    import os
    override = os.environ.get("BLOCKING_LIMIT_OVERRIDE")
    if override:
        parsed = int(override)
        if parsed > 0:
            return parsed
    return get_effective_context_window(context_window) - MANUAL_COMPACT_BUFFER_TOKENS


@dataclass
class TokenWarningState:
    """
    Claude Code: calculateTokenWarningState() return type.
    Five-level warning system for context usage.
    """
    percent_left: int
    is_above_warning: bool
    is_above_error: bool
    is_above_auto_compact: bool
    is_at_blocking_limit: bool


def calculate_token_warning_state(
    token_usage: int,
    context_window: int,
) -> TokenWarningState:
    """
    Calculate token warning states.
    Claude Code: calculateTokenWarningState() in autoCompact.ts lines 93-145.

    Thresholds:
      - Warning: threshold - 20K
      - Error: threshold - 20K (same as warning in Claude Code)
      - AutoCompact: threshold (effectiveWindow - 13K)
      - Blocking: effectiveWindow - 3K (hard limit, synthetic 413)
    """
    auto_compact_threshold = get_auto_compact_threshold(context_window)
    threshold = auto_compact_threshold

    percent_left = max(0, round(((threshold - token_usage) / threshold) * 100)) if threshold > 0 else 0

    warning_threshold = threshold - WARNING_THRESHOLD_BUFFER_TOKENS
    error_threshold = threshold - ERROR_THRESHOLD_BUFFER_TOKENS
    blocking_limit = get_blocking_limit(context_window)

    return TokenWarningState(
        percent_left=percent_left,
        is_above_warning=token_usage >= warning_threshold,
        is_above_error=token_usage >= error_threshold,
        is_above_auto_compact=token_usage >= auto_compact_threshold,
        is_at_blocking_limit=token_usage >= blocking_limit,
    )


# ── Token estimation ──────────────────────────────────────────────────

def estimate_messages_tokens(messages: list, model: str = "") -> int:
    """
    Estimate total token count for messages.
    Claude Code pads by 4/3 to be conservative (microCompact.ts line 204).
    """
    total = 0
    for msg in messages:
        if msg.token_count and msg.token_count > 0:
            total += msg.token_count
        else:
            total += count_tokens(msg.content, model)

    # Pad by 4/3 to be conservative (Claude Code pattern)
    return int(total * 4 / 3)


# ── Should compact check ──────────────────────────────────────────────

def should_auto_compact(
    messages: list,
    context_window: int,
    tracking: AutoCompactTracking | None,
    model: str = "",
) -> bool:
    """
    Check if autocompact should trigger.
    Claude Code: shouldAutoCompact() in autoCompact.ts line 160.

    Returns False if:
      - Circuit breaker tripped (>= 3 consecutive failures)
      - Token count under threshold
    """
    # Circuit breaker (Claude Code line 260-264)
    if tracking and tracking.consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        logger.warning(
            f"Autocompact circuit breaker: {tracking.consecutive_failures} "
            f"consecutive failures, skipping"
        )
        return False

    token_count = estimate_messages_tokens(messages, model)
    threshold = get_auto_compact_threshold(context_window)

    logger.debug(
        f"Autocompact check: tokens={token_count} threshold={threshold} "
        f"context_window={context_window}"
    )

    return token_count >= threshold


# ── Main compaction function ──────────────────────────────────────────

async def auto_compact_if_needed(
    messages: list,
    system_prompt: str,
    context_window: int,
    tracking: AutoCompactTracking | None,
    model: str,
    compact_fn: Callable[[list, str], Awaitable[str]],
) -> CompactionResult | None:
    """
    If token count exceeds threshold, summarize conversation via API call.

    Claude Code: autoCompactIfNeeded() in autoCompact.ts line 241.

    Args:
        messages: Current conversation messages
        system_prompt: Current system prompt (passed to compact call)
        context_window: Model's context window size in tokens
        tracking: Compaction state from previous iterations
        model: Model name for token counting
        compact_fn: async (messages, system_prompt) -> summary_string
                   This calls the LLM to generate the summary.
                   Injected as a dependency so the query loop can provide it.

    Returns:
        CompactionResult if compacted, None if under threshold.
    """
    if not should_auto_compact(messages, context_window, tracking, model):
        return None

    pre_compact_tokens = estimate_messages_tokens(messages, model)
    threshold = get_auto_compact_threshold(context_window)

    logger.info(
        f"Autocompact triggered: {pre_compact_tokens} tokens >= {threshold} threshold"
    )

    # PreCompact hook (CC: executePreCompactHooks, trigger='auto'). May return
    # custom instructions that get folded into the summarization prompt.
    custom_instructions = await _fire_pre_compact(trigger="auto")

    try:
        # Call the LLM to generate a conversation summary. Pass PreCompact's
        # custom instructions when the compact_fn accepts them (the injected
        # create_compact_fn does; older 2-arg fns are tolerated).
        try:
            summary = await compact_fn(messages, system_prompt, custom_instructions)
        except TypeError:
            summary = await compact_fn(messages, system_prompt)

        # PostCompact hook (CC: executePostCompactHooks) — receives the summary.
        await _fire_post_compact(trigger="auto", compact_summary=summary)

        # Build post-compact messages: [summary] + last N messages
        compacted = build_post_compact_messages(messages, summary)
        post_compact_tokens = estimate_messages_tokens(compacted, model)

        logger.info(
            f"Autocompact succeeded: {pre_compact_tokens} -> {post_compact_tokens} tokens "
            f"({pre_compact_tokens - post_compact_tokens} freed)"
        )

        return CompactionResult(
            messages=compacted,
            summary=summary,
            tracking=AutoCompactTracking(
                compacted=True,
                turn_counter=0,
                consecutive_failures=0,  # Reset on success (Claude Code line 333)
            ),
            pre_compact_tokens=pre_compact_tokens,
            post_compact_tokens=post_compact_tokens,
        )

    except Exception as e:
        # Increment failure count for circuit breaker (Claude Code line 341)
        prev_failures = tracking.consecutive_failures if tracking else 0
        next_failures = prev_failures + 1

        logger.warning(
            f"Autocompact failed ({next_failures}/{MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES}): {e}"
        )

        if next_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
            logger.warning(
                "Autocompact circuit breaker tripped — skipping future attempts this session"
            )

        return CompactionResult(
            messages=messages,  # Return unchanged messages
            summary="",
            tracking=AutoCompactTracking(
                compacted=False,
                turn_counter=0,
                consecutive_failures=next_failures,
            ),
            pre_compact_tokens=pre_compact_tokens,
            post_compact_tokens=pre_compact_tokens,  # No change
        )


# ── Post-compact message building ────────────────────────────────────

def build_post_compact_messages(
    messages: list,
    summary: str,
) -> list:
    """
    Build the message list after compaction.

    Claude Code: buildPostCompactMessages() in compact.ts line 330.

    Structure: [summary_message, ...last_N_messages]

    Keeps the most recent messages so the model has immediate context.
    Claude Code keeps attachments, hook results, and file snapshots too,
    but we start with the core pattern: summary + recent messages.
    """
    from services.compact.prompt import get_compact_user_summary_message

    # Single source of truth for the resume message (CC: getCompactUserSummaryMessage).
    summary_content = get_compact_user_summary_message(
        summary, suppress_follow_up_questions=True
    )

    # Import here to avoid circular import
    from context.manager import MessageItem
    summary_msg = MessageItem(
        role="user",
        content=summary_content,
        token_count=count_tokens(summary_content),
    )

    # Keep the most recent messages for context continuity
    # Default: 6 messages (3 turns of user+assistant)
    recent = messages[-POST_COMPACT_KEEP_RECENT:] if len(messages) > POST_COMPACT_KEEP_RECENT else []

    return [summary_msg] + recent


# ── Compaction lifecycle hooks (CC parity) ───────────────────────────

def _compact_hook_executor():
    """Build a HookExecutor from the frozen session snapshot, or None.

    Compaction runs deep in the context manager with no HookExecutor in scope,
    so we read the module-global hooks snapshot — the same source CC's compact
    pipeline reaches for executePreCompactHooks/executePostCompactHooks.
    """
    from hooks.config import get_hooks_from_snapshot
    from hooks.executor import HookExecutor

    snapshot = get_hooks_from_snapshot()
    if not snapshot:
        return None
    return HookExecutor(snapshot)


async def _fire_pre_compact(trigger: str) -> str | None:
    """Fire PreCompact (CC: executePreCompactHooks). Returns custom instructions
    a hook may supply to steer the summary, or None."""
    from core_types.hooks import HookEvent

    executor = _compact_hook_executor()
    if executor is None:
        return None
    try:
        result = await executor.run_event_hooks(
            event=HookEvent.PRE_COMPACT.value,
            data={"trigger": trigger, "custom_instructions": None},
        )
        # A PreCompact hook returns instructions via stdout → additional_context.
        return result.additional_context or None
    except Exception:
        logger.debug("PreCompact hook failed", exc_info=True)
        return None


async def _fire_post_compact(trigger: str, compact_summary: str) -> None:
    """Fire PostCompact (CC: executePostCompactHooks) with the produced summary."""
    from core_types.hooks import HookEvent

    executor = _compact_hook_executor()
    if executor is None:
        return
    try:
        await executor.run_event_hooks(
            event=HookEvent.POST_COMPACT.value,
            data={"trigger": trigger, "compact_summary": compact_summary},
        )
    except Exception:
        logger.debug("PostCompact hook failed", exc_info=True)
