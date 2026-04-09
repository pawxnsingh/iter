"""
Snip compact: Remove oldest messages entirely to free tokens.

Source: Claude Code src/services/compact/snipCompact.ts (HISTORY_SNIP feature)

This is the cheapest compaction after microcompact. Instead of summarizing,
it simply removes the oldest message groups. Runs BEFORE microcompact in
the query loop.

Algorithm:
  1. Group messages by API round (user→assistant→tool_results)
  2. If over threshold, drop oldest groups until under
  3. Preserve at least the last N groups for context continuity
"""

from __future__ import annotations

import logging

from context.manager import MessageItem
from services.compact.auto_compact import (
    estimate_messages_tokens,
    get_auto_compact_threshold,
)

logger = logging.getLogger(__name__)

# Keep at least this many message groups after snipping
MIN_GROUPS_TO_KEEP = 4


def snip_compact_if_needed(
    messages: list[MessageItem],
    context_window: int,
    model: str = "",
) -> tuple[list[MessageItem], int]:
    """
    Remove oldest message groups if over threshold.

    Returns: (messages, tokens_freed)
    """
    threshold = get_auto_compact_threshold(context_window)
    current_tokens = estimate_messages_tokens(messages, model)

    if current_tokens < threshold:
        return messages, 0

    # Group messages by turns (user + assistant + tool results = one group)
    groups = _group_by_turn(messages)

    if len(groups) <= MIN_GROUPS_TO_KEEP:
        return messages, 0

    # Drop oldest groups until under threshold
    tokens_freed = 0
    groups_dropped = 0

    while len(groups) > MIN_GROUPS_TO_KEEP:
        group = groups[0]
        group_tokens = sum(
            estimate_messages_tokens([m], model) for m in group
        )

        groups.pop(0)
        groups_dropped += 1
        tokens_freed += group_tokens

        remaining_tokens = current_tokens - tokens_freed
        if remaining_tokens < threshold:
            break

    if groups_dropped > 0:
        logger.info(f"Snip compact: dropped {groups_dropped} groups, freed {tokens_freed} tokens")

    # Flatten groups back to message list
    result = []
    for group in groups:
        result.extend(group)

    return result, tokens_freed


def _group_by_turn(messages: list[MessageItem]) -> list[list[MessageItem]]:
    """
    Group messages by API round.
    Each group: [user_message, assistant_message, tool_result_1, tool_result_2, ...]
    """
    groups: list[list[MessageItem]] = []
    current_group: list[MessageItem] = []

    for msg in messages:
        if msg.role == "user" and current_group:
            groups.append(current_group)
            current_group = []
        current_group.append(msg)

    if current_group:
        groups.append(current_group)

    return groups
