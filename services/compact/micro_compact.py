"""
Microcompact: Layer 1 of Claude Code's 4-layer compaction system.

Source: src/services/compact/microCompact.ts

Zero-cost context savings — no API call, just string replacement.
Replaces old tool results with "[Old tool result content cleared]".

Rules (from Claude Code):
  - Only clear tools in COMPACTABLE_TOOLS set
  - Only clear results OLDER than the most recent assistant message
  - Images under 2000 tokens are preserved (not applicable for us yet)
  - Skip tiny results (< 500 chars — not worth the overhead)
"""

from __future__ import annotations

# Claude Code: TIME_BASED_MC_CLEARED_MESSAGE
CLEARED_MESSAGE = "[Old tool result content cleared]"

# Claude Code exact set from microCompact.ts lines 40-50
# Only these tools get their results cleared
COMPACTABLE_TOOLS = {
    "Read",
    "Bash",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "Edit",
    "Write",
}

# Don't bother clearing results smaller than this
MIN_CONTENT_LENGTH = 500

# Claude Code: IMAGE_MAX_TOKEN_SIZE = 2000
IMAGE_MAX_TOKEN_SIZE = 2000


def micro_compact(messages: list) -> list:
    """
    Replace old tool results with placeholder to save tokens.

    This is Claude Code's cheapest compaction layer — runs BEFORE every
    API call, costs nothing (no API call).

    The algorithm mirrors microcompactMessages() in microCompact.ts:
    1. Find the index of the last assistant message (the boundary)
    2. For every tool result message BEFORE that boundary:
       - If the tool is in COMPACTABLE_TOOLS
       - If the content is long enough to be worth clearing
       - Replace content with CLEARED_MESSAGE
    3. Return the modified message list

    Why this works: Old tool results (file reads, grep outputs) are huge
    but the model already processed them. Clearing them saves thousands
    of tokens for free.
    """
    if len(messages) < 4:
        return messages

    # Find the boundary: index of last assistant message
    last_assistant_idx = _find_last_assistant_index(messages)
    if last_assistant_idx <= 0:
        return messages

    # Collect tool names from assistant messages so we can filter
    # Only clear results from COMPACTABLE_TOOLS
    tool_names_by_call_id = _build_tool_name_map(messages)

    result = []
    for i, msg in enumerate(messages):
        if _should_clear(msg, i, last_assistant_idx, tool_names_by_call_id):
            # Create a copy with cleared content
            # We use the same class as the input (MessageItem) via dataclasses.replace
            from dataclasses import replace
            try:
                cleared = replace(msg, content=CLEARED_MESSAGE, token_count=8)
            except TypeError:
                # Fallback if replace doesn't work (non-dataclass)
                cleared = msg
                cleared.content = CLEARED_MESSAGE
                cleared.token_count = 8
            result.append(cleared)
        else:
            result.append(msg)

    return result


def _find_last_assistant_index(messages: list) -> int:
    """Find index of the most recent assistant message."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "assistant":
            return i
    return -1


def _build_tool_name_map(messages: list) -> dict[str, str]:
    """
    Build a map of tool_call_id -> tool_name from assistant messages.

    Claude Code tracks this via tool_use blocks in assistant messages.
    We extract it from the tool_calls field on assistant messages.
    """
    name_map: dict[str, str] = {}
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                call_id = tc.get("id", "")
                func = tc.get("function", {})
                name = func.get("name", "")
                if call_id and name:
                    name_map[call_id] = name
    return name_map


def _should_clear(
    msg,
    index: int,
    last_assistant_idx: int,
    tool_names: dict[str, str],
) -> bool:
    """Decide if a message's content should be cleared."""
    # Only clear tool result messages
    if msg.role != "tool":
        return False

    # Only clear messages BEFORE the last assistant message
    if index >= last_assistant_idx:
        return False

    # Don't re-clear already cleared messages
    if msg.content == CLEARED_MESSAGE:
        return False

    # Skip tiny results (not worth the overhead)
    if len(msg.content) <= MIN_CONTENT_LENGTH:
        return False

    # Check if this tool is in the compactable set
    tool_name = tool_names.get(msg.tool_call_id or "", "")
    if tool_name and tool_name not in COMPACTABLE_TOOLS:
        return False

    # If we don't know the tool name (no mapping found), clear it anyway
    # This is safe because the placeholder tells the model what happened
    return True
