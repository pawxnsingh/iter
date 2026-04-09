"""
Compact: The actual LLM call that summarizes a conversation.

Source: src/services/compact/compact.ts

This function takes the full conversation, sends it to the LLM with
the compaction prompt, and returns the summary text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from client.llm_client import LLMClient
from client.response import StreamEventType
from services.compact.prompt import get_compact_prompt


async def compact_conversation(
    messages: list,
    system_prompt: str,
    client: LLMClient,
    model: str,
) -> str:
    """
    Call the LLM to summarize the conversation.

    This is the expensive compaction operation — it makes an actual API call.
    Claude Code: compactConversation() in compact.ts line 387.

    The flow:
    1. Format messages into a readable conversation transcript
    2. Send to LLM with the compaction prompt (9-section summary request)
    3. Return the raw summary text (caller handles formatting)
    """
    compact_prompt = get_compact_prompt()

    # Build the conversation transcript for the model to summarize
    conversation_text = _format_conversation_for_compact(messages)

    # Build the compact request messages
    compact_messages = [
        {"role": "system", "content": compact_prompt},
        {"role": "user", "content": f"Here is the conversation to summarize:\n\n{conversation_text}"},
    ]

    # Call LLM (non-streaming for compaction — we need the full response)
    summary_parts = []
    async for event in client.chat_completion(
        messages=compact_messages,
        tools=None,  # No tools during compaction
        stream=True,
    ):
        if event.type == StreamEventType.TEXT_DELTA and event.text_delta:
            summary_parts.append(event.text_delta.content)
        elif event.type == StreamEventType.ERROR:
            raise RuntimeError(f"Compaction API error: {event.error}")

    summary = "".join(summary_parts)
    if not summary.strip():
        raise RuntimeError("Compaction returned empty summary")

    return summary


def _format_conversation_for_compact(messages: list) -> str:
    """
    Format messages into a readable transcript for the compaction model.

    Each message becomes a labeled section so the summarizer can
    distinguish user requests from tool outputs from assistant responses.
    """
    parts = []
    for msg in messages:
        role_label = msg.role.upper()
        content = msg.content

        # Truncate very long tool results in the transcript too
        # (they're the reason we're compacting in the first place)
        if msg.role == "tool" and len(content) > 5000:
            content = content[:5000] + "\n... [truncated for compaction]"

        if msg.tool_call_id:
            parts.append(f"[{role_label} (tool_call_id: {msg.tool_call_id})]:\n{content}")
        elif msg.tool_calls:
            tool_names = []
            for tc in msg.tool_calls:
                func = tc.get("function", {})
                tool_names.append(func.get("name", "unknown"))
            parts.append(f"[{role_label} -> called tools: {', '.join(tool_names)}]:\n{content}")
        else:
            parts.append(f"[{role_label}]:\n{content}")

    return "\n\n---\n\n".join(parts)


def create_compact_fn(client: LLMClient, model: str):
    """
    Factory that creates the compact_fn for dependency injection.

    The context manager needs a compact function but shouldn't know
    about the LLM client directly. This factory creates the function
    that the agent injects into the context manager.

    This is the same dependency injection pattern Claude Code uses
    with QueryDeps.
    """
    async def compact_fn(messages: list, system_prompt: str) -> str:
        return await compact_conversation(messages, system_prompt, client, model)

    return compact_fn
