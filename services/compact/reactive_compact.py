"""
Reactive compact: Emergency compaction triggered by API 413 errors.

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
from utils.text import count_tokens

logger = logging.getLogger(__name__)


async def try_reactive_compact(
    messages: list[MessageItem],
    compact_fn,
    system_prompt: str,
    transcript_path: str | None = None,
) -> list[MessageItem] | None:
    """
    Attempt emergency compaction after a 413 error.

    Returns compacted messages if successful, None if failed.
    """
    if len(messages) < 3:
        return None

    pre_tokens = estimate_messages_tokens(messages)
    logger.info(f"Reactive compact triggered: {pre_tokens} tokens")

    try:
        stripped = _strip_images(messages)

        summary = await compact_fn(stripped, system_prompt)

        if not summary or not summary.strip():
            logger.warning("Reactive compact returned empty summary")
            return None

        from services.compact.auto_compact import build_post_compact_messages
        compacted = build_post_compact_messages(messages, summary, transcript_path)
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

    Images are replaced with [image] marker so the summary notes
    that an image was shared, without the token cost.
    """
    result = []
    for msg in messages:
        if "[image]" in msg.content or "data:image" in msg.content:
            content = msg.content
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
