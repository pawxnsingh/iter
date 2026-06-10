"""
Reactive compact: Emergency compaction triggered by API 413 errors.

Source: Claude Code src/services/compact/reactiveCompact.ts

This is Layer 4 — the last resort when the API rejects the prompt.
Unlike autocompact (proactive), reactive compact fires AFTER a 413 error.

Flow:
  1. API returns 413 (prompt too long)
  2. Strip images from messages (they're token-heavy)
  3. Run full conversation summarization
  4. Replace old messages with summary
  5. Retry the API call
"""

from __future__ import annotations

import logging

from context.manager import MessageItem
from services.compact.auto_compact import estimate_messages_tokens
from services.compact.prompt import get_compact_user_summary_message
from utils.text import count_tokens

logger = logging.getLogger(__name__)


async def try_reactive_compact(
    messages: list[MessageItem],
    compact_fn,
    system_prompt: str,
) -> list[MessageItem] | None:
    """
    Attempt emergency compaction after a 413 error.
    Claude Code: tryReactiveCompact() in reactiveCompact.ts

    Returns compacted messages if successful, None if failed.
    """
    if len(messages) < 3:
        return None

    pre_tokens = estimate_messages_tokens(messages)
    logger.info(f"Reactive compact triggered: {pre_tokens} tokens")

    try:
        # Strip images from messages before compaction
        stripped = _strip_images(messages)

        # Run full compaction
        summary = await compact_fn(stripped, system_prompt)

        if not summary or not summary.strip():
            logger.warning("Reactive compact returned empty summary")
            return None

        # Build post-compact resume message (single source of truth).
        summary_content = get_compact_user_summary_message(
            summary, suppress_follow_up_questions=True
        )

        summary_msg = MessageItem(
            role="user",
            content=summary_content,
            token_count=count_tokens(summary_content),
        )

        # Keep last 4 messages for immediate context
        recent = messages[-4:] if len(messages) > 4 else []

        compacted = [summary_msg] + recent
        post_tokens = estimate_messages_tokens(compacted)

        logger.info(
            f"Reactive compact succeeded: {pre_tokens} -> {post_tokens} tokens "
            f"({pre_tokens - post_tokens} freed)"
        )

        return compacted

    except Exception as e:
        logger.error(f"Reactive compact failed: {e}")
        return None


def _strip_images(messages: list[MessageItem]) -> list[MessageItem]:
    """
    Strip image references from messages before compaction.
    Claude Code: stripImagesFromMessages() in compact.ts

    Images are replaced with [image] marker so the summary notes
    that an image was shared, without the token cost.
    """
    result = []
    for msg in messages:
        if "[image]" in msg.content or "data:image" in msg.content:
            # Replace image content with marker
            content = msg.content
            # Simple image stripping — replace base64 image data
            import re
            content = re.sub(
                r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+',
                '[image]',
                content,
            )
            result.append(MessageItem(
                role=msg.role,
                content=content,
                tool_call_id=msg.tool_call_id,
                tool_calls=msg.tool_calls,
                token_count=count_tokens(content),
                turn_number=msg.turn_number,
            ))
        else:
            result.append(msg)
    return result
