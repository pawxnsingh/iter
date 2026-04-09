"""
Multi-provider LLM client.

Supports: Azure OpenAI, OpenAI, Anthropic (Claude).
Provider is configured via config or environment variables.

Claude Code equivalent: src/services/api/claude.ts
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncGenerator

from client.response import (
    StreamEvent,
    StreamEventType,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    parse_arguments_response,
)
from config.config import Config

logger = logging.getLogger(__name__)


def _sanitize_error(msg: str) -> str:
    """Strip potential API keys/tokens from error messages."""
    import re
    msg = re.sub(r'(sk-[a-zA-Z0-9]{20,})', 'sk-***', msg)
    msg = re.sub(r'(key|token|secret|password)[\s=:]+\S+', r'\1=***', msg, flags=re.IGNORECASE)
    return msg[:500]  # Truncate long errors


class LLMClient:
    """
    Multi-provider LLM client with retry logic.

    Detects provider from config/env:
      - ANTHROPIC_API_KEY → Anthropic (Claude)
      - OPENAI_API_KEY → OpenAI
      - AZURE_OPENAI_API_KEY → Azure OpenAI
    """

    def __init__(self, config: Config) -> None:
        self._client = None
        self._max_retries = 3
        self.config = config
        self._provider = self._detect_provider()
        logger.info(f"LLM provider: {self._provider}")

    def _detect_provider(self) -> str:
        """Detect which LLM provider to use."""
        # Explicit config override
        provider = os.environ.get("LLM_PROVIDER", "").lower()
        if provider in ("anthropic", "openai", "azure"):
            return provider

        # Auto-detect from available keys
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if os.environ.get("AZURE_OPENAI_API_KEY"):
            return "azure"

        # Fallback
        return "azure"

    def _get_client(self):
        """Lazily create the appropriate client."""
        if self._client is not None:
            return self._client

        if self._provider == "anthropic":
            import anthropic
            self._client = anthropic.AsyncAnthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
            )
        elif self._provider == "openai":
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
            )
        elif self._provider == "azure":
            from openai import AsyncAzureOpenAI
            self._client = AsyncAzureOpenAI(
                api_key=self.config.api_key,
                azure_endpoint=self.config.base_url,
                api_version=self.config.api_version,
            )
        else:
            raise ValueError(f"Unknown LLM provider: {self._provider}")

        return self._client

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool = True,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Send a chat completion request to the configured provider.
        Yields StreamEvent objects for real-time streaming.
        Includes retry logic with exponential backoff.
        """
        for attempt in range(self._max_retries + 1):
            try:
                if self._provider == "anthropic":
                    async for event in self._anthropic_stream(messages, tools):
                        yield event
                else:
                    async for event in self._openai_stream(messages, tools, stream):
                        yield event
                return

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "rate" in error_str and "limit" in error_str
                is_connection = "connection" in error_str

                if (is_rate_limit or is_connection) and attempt < self._max_retries:
                    wait_time = 2 ** attempt
                    logger.warning(f"Retrying in {wait_time}s (attempt {attempt + 1}): {type(e).__name__}")
                    await asyncio.sleep(wait_time)
                    # CRITICAL: On retry, we restart the full stream.
                    # Any events already yielded in this attempt are partial/incomplete.
                    # The caller must handle potential duplicate partial content on retry.
                    # We yield a retry marker so the caller can reset state if needed.
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        error=f"__RETRY_RESET__:attempt={attempt + 1}",
                    )
                else:
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        error=f"API error: {type(e).__name__}: {_sanitize_error(str(e))}",
                    )
                    return

    # ── OpenAI / Azure OpenAI streaming ─────────────────────────────

    async def _openai_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool = True,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream from OpenAI or Azure OpenAI."""
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "messages": messages,
            "model": self.config.model_name,
            "stream": stream,
        }

        if tools:
            kwargs["tools"] = self._build_openai_tools(tools)
            kwargs["tool_choice"] = "auto"

        if stream:
            async for event in self._openai_stream_response(client, kwargs):
                yield event
        else:
            async for event in self._openai_non_stream_response(client, kwargs):
                yield event

    def _build_openai_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]

    async def _openai_stream_response(
        self, client, kwargs: dict[str, Any]
    ) -> AsyncGenerator[StreamEvent, None]:
        response = await client.chat.completions.create(**kwargs)

        usage: TokenUsage | None = None
        finish_reason: str | None = None
        tool_calls: dict[int, dict[str, Any]] = {}

        async for chunk in response:
            if hasattr(chunk, "usage") and chunk.usage:
                usage = TokenUsage(
                    prompt_token=getattr(chunk.usage, "prompt_tokens", 0),
                    completion_tokens=getattr(chunk.usage, "completion_tokens", 0),
                    total_tokens=getattr(chunk.usage, "total_tokens", 0),
                    cached_tokens=getattr(
                        getattr(chunk.usage, "prompt_tokens_details", None),
                        "cached_tokens", 0
                    ) or 0,
                )

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if choice.finish_reason:
                finish_reason = choice.finish_reason

            if delta.content:
                yield StreamEvent(
                    type=StreamEventType.TEXT_DELTA,
                    text_delta=TextDelta(content=delta.content),
                )

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls:
                        tool_calls[idx] = {"id": tc_delta.id or "", "name": "", "arguments": ""}

                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls[idx]["name"] = tc_delta.function.name
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_START,
                                tool_call_delta=ToolCallDelta(
                                    call_id=tool_calls[idx]["id"],
                                    name=tool_calls[idx]["name"],
                                ),
                            )
                        if tc_delta.function.arguments:
                            tool_calls[idx]["arguments"] += tc_delta.function.arguments

        for idx, tc in tool_calls.items():
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL_COMPLETED,
                tool_call=ToolCall(
                    call_id=tc["id"],
                    name=tc["name"],
                    arguments=parse_arguments_response(tc["arguments"]),
                ),
            )

        yield StreamEvent(
            type=StreamEventType.MESSAGE_COMPLETE,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def _openai_non_stream_response(
        self, client, kwargs: dict[str, Any]
    ) -> AsyncGenerator[StreamEvent, None]:
        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        if message.content:
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA,
                text_delta=TextDelta(content=message.content),
            )

        if message.tool_calls:
            for tc in message.tool_calls:
                yield StreamEvent(
                    type=StreamEventType.TOOL_CALL_COMPLETED,
                    tool_call=ToolCall(
                        call_id=tc.id,
                        name=tc.function.name,
                        arguments=parse_arguments_response(tc.function.arguments),
                    ),
                )

        token_usage = None
        if response.usage:
            token_usage = TokenUsage(
                prompt_token=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )

        yield StreamEvent(
            type=StreamEventType.MESSAGE_COMPLETE,
            finish_reason=choice.finish_reason,
            usage=token_usage,
        )

    # ── Anthropic (Claude) streaming ─────────────────────────────────

    async def _anthropic_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream from Anthropic Claude API."""
        client = self._get_client()

        # Separate system message from conversation
        system_content = ""
        conversation = []
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                conversation.append(msg)

        # Build Anthropic tools format
        anthropic_tools = []
        if tools:
            for tool in tools:
                anthropic_tools.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
                })

        kwargs: dict[str, Any] = {
            "model": self.config.model_name,
            "max_tokens": 8192,
            "messages": conversation,
        }
        if system_content:
            kwargs["system"] = system_content
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        async with client.messages.stream(**kwargs) as stream:
            current_tool_id = ""
            current_tool_name = ""
            current_tool_input = ""

            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "text":
                        pass
                    elif block.type == "tool_use":
                        current_tool_id = block.id
                        current_tool_name = block.name
                        current_tool_input = ""
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_START,
                            tool_call_delta=ToolCallDelta(
                                call_id=current_tool_id,
                                name=current_tool_name,
                            ),
                        )

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamEvent(
                            type=StreamEventType.TEXT_DELTA,
                            text_delta=TextDelta(content=delta.text),
                        )
                    elif delta.type == "input_json_delta":
                        current_tool_input += delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool_id and current_tool_name:
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_COMPLETED,
                            tool_call=ToolCall(
                                call_id=current_tool_id,
                                name=current_tool_name,
                                arguments=parse_arguments_response(current_tool_input),
                            ),
                        )
                        current_tool_id = ""
                        current_tool_name = ""
                        current_tool_input = ""

                elif event.type == "message_stop":
                    pass

            # Get final message for usage
            final = await stream.get_final_message()
            usage = TokenUsage(
                prompt_token=final.usage.input_tokens,
                completion_tokens=final.usage.output_tokens,
                total_tokens=final.usage.input_tokens + final.usage.output_tokens,
            )

            yield StreamEvent(
                type=StreamEventType.MESSAGE_COMPLETE,
                finish_reason=final.stop_reason or "end_turn",
                usage=usage,
            )
