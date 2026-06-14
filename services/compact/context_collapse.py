"""
Context collapse: Hierarchical archival of old messages.

Source: src/services/contextCollapse/ (CONTEXT_COLLAPSE feature)

More granular than full summarization. Instead of replacing ALL old messages
with one summary, context collapse archives individual conversation segments
while preserving their summaries as lightweight placeholders.

This runs BEFORE autocompact so that if collapse frees enough space,
autocompact is skipped (preserving granular context).

Collapse levels:
  1. Tool results → collapsed to one-line summaries
  2. Full turns → collapsed to summary paragraphs
  3. Turn groups → collapsed to high-level summaries
"""

from __future__ import annotations

import logging

from context.manager import MessageItem
from services.compact.auto_compact import (
    estimate_messages_tokens,
    get_auto_compact_threshold,
)
from utils.text import estimate_token

logger = logging.getLogger(__name__)

COLLAPSE_TARGET_RATIO = 0.90


def apply_context_collapse(
    messages: list[MessageItem],
    context_window: int,
    model: str = "",
) -> list[MessageItem]:
    """
    Apply hierarchical context collapse.

    Progressively summarizes old message groups into compact placeholders
    while keeping recent context intact.
    """
    threshold = get_auto_compact_threshold(context_window)
    current_tokens = estimate_messages_tokens(messages, model)
    target = int(threshold * COLLAPSE_TARGET_RATIO)

    if current_tokens < target:
        return messages

    logger.info(f"Context collapse: {current_tokens} tokens > {target} target")

    messages = _collapse_tool_results(messages, target, model)
    if estimate_messages_tokens(messages, model) < target:
        return messages

    messages = _collapse_assistant_messages(messages, target, model)

    return messages


def _collapse_tool_results(
    messages: list[MessageItem],
    target: int,
    model: str,
) -> list[MessageItem]:
    """
    Replace old tool results with one-line summaries.
    More granular than microcompact — keeps the tool name and outcome
    rather than just "[Old tool result content cleared]".
    """
    if len(messages) < 6:
        return messages

    boundary = max(0, len(messages) - 8)
    result = []

    for i, msg in enumerate(messages):
        if i < boundary and msg.role == "tool" and len(msg.content) > 200:
            first_line = msg.content.split("\n")[0][:100]
            collapsed_content = f"[Tool result collapsed: {first_line}...]"
            result.append(MessageItem(
                role=msg.role,
                content=collapsed_content,
                tool_call_id=msg.tool_call_id,
                tool_calls=msg.tool_calls,
                token_count=estimate_token(collapsed_content),
                turn_number=msg.turn_number,
            ))
        else:
            result.append(msg)

    freed = estimate_messages_tokens(messages, model) - estimate_messages_tokens(result, model)
    if freed > 0:
        logger.debug(f"Context collapse phase 1: freed {freed} tokens")

    return result


def _collapse_assistant_messages(
    messages: list[MessageItem],
    target: int,
    model: str,
) -> list[MessageItem]:
    """
    Collapse old assistant messages to brief summaries.
    Keeps the first sentence or two, drops the rest.
    """
    if len(messages) < 10:
        return messages

    boundary = max(0, len(messages) - 10)
    result = []

    for i, msg in enumerate(messages):
        if i < boundary and msg.role == "assistant" and len(msg.content) > 500:
            summary = msg.content[:200].rsplit(".", 1)[0] + "."
            collapsed = f"[Earlier response collapsed] {summary}"
            result.append(MessageItem(
                role=msg.role,
                content=collapsed,
                tool_calls=msg.tool_calls,
                token_count=estimate_token(collapsed),
                turn_number=msg.turn_number,
            ))
        else:
            result.append(msg)

    return result
