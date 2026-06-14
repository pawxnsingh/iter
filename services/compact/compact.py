"""
Compact: The actual LLM call that summarizes a conversation.

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

SUMMARIZER_SYSTEM_PROMPT = (
    "You are a helpful AI assistant tasked with summarizing conversations."
)


async def compact_conversation(
    messages: list,
    system_prompt: str,
    client: LLMClient,
    model: str,
    custom_instructions: str | None = None,
) -> str:
    """
    Call the LLM to summarize the conversation.

    This is the expensive compaction operation — it makes an actual API call.

    sends the REAL structured conversation to the summarizer, NOT a flattened
    text transcript:
      - systemPrompt: a short fixed string (SUMMARIZER_SYSTEM_PROMPT).
      - messages: the actual prior messages (full content, real roles/tool
        blocks), with images stripped, followed by the 9-section compact prompt
        as the trailing USER message (summaryRequest).

    custom_instructions, when present, are folded into the compaction prompt.
    They originate from a PreCompact hook.
    """
    compact_prompt = get_compact_prompt(custom_instructions)

    compact_messages = [
        {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
        *(_to_text_only_dict(msg) for msg in messages),
        {"role": "user", "content": compact_prompt},
    ]

    summary_parts = []
    async for event in client.chat_completion(
        messages=compact_messages,
        tools=None,
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


def _to_text_only_dict(msg) -> dict:
    """Convert a MessageItem to its API dict, stripping image blocks (
    stripImagesFromMessages) so we don't ship base64 to the summarizer. Keeps
    real roles + tool_call/tool_result structure for high-fidelity summaries."""
    out: dict = {"role": msg.role}
    if msg.tool_call_id:
        out["tool_call_id"] = msg.tool_call_id
    if msg.tool_calls:
        out["tool_calls"] = msg.tool_calls
    out["content"] = msg.content or ""
    return out


def create_compact_fn(client: LLMClient, model: str):
    """
    Factory that creates the compact_fn for dependency injection.

    The context manager needs a compact function but shouldn't know
    about the LLM client directly. This factory creates the function
    that the agent injects into the context manager.

    This is the same dependency injection pattern uses
    with QueryDeps.
    """
    async def compact_fn(messages: list, system_prompt: str, custom_instructions: str | None = None) -> str:
        return await compact_conversation(messages, system_prompt, client, model, custom_instructions)

    return compact_fn
