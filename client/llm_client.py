"""
Multi-provider LLM client.

Supports: Azure OpenAI, OpenAI, Anthropic.
Provider is configured via config or environment variables.

"""

from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
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
from prompts.system import DYNAMIC_BOUNDARY

logger = logging.getLogger(__name__)


class ErrorKind(str, Enum):
    """Provider-agnostic error taxonomy used by recovery logic in query_loop.

    Each provider's exceptions/error strings map to one of these. Callers
    branch on the enum, never on raw strings — that's how multi-provider
    support stays clean.
    """

    PROMPT_TOO_LONG = "prompt_too_long"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    OVERLOADED = "overloaded"
    AUTH = "auth"
    INVALID_REQUEST = "invalid_request"
    SERVER_ERROR = "server_error"
    OTHER = "other"


def classify_error(exc_or_msg: Exception | str) -> ErrorKind:
    """Map any provider's exception/error message to ErrorKind.

    Centralised here so query_loop never string-matches error wording from
    a specific provider. If the Anthropic / OpenAI / Azure SDKs change
    their error formats, only this function needs updating.
    """
    text = str(exc_or_msg).lower()

    if any(s in text for s in (
        "prompt is too long", "context_length_exceeded", "context length",
        "maximum context length", "input is too long", "413",
    )):
        return ErrorKind.PROMPT_TOO_LONG

    if any(s in text for s in (
        "max output tokens", "max_tokens", "output token limit",
        "max_output_tokens",
    )):
        return ErrorKind.MAX_OUTPUT_TOKENS

    if any(s in text for s in (
        "overloaded", "529", "rate_limit", "rate limit", "too many requests",
        "capacity", "503",
    )):
        return ErrorKind.OVERLOADED

    if any(s in text for s in (
        "unauthorized", "401", "forbidden", "403", "invalid api key",
        "authentication",
    )):
        return ErrorKind.AUTH

    if any(s in text for s in (
        "invalid request", "400", "bad request", "validation",
    )):
        return ErrorKind.INVALID_REQUEST

    if any(s in text for s in ("500", "502", "504", "server error")):
        return ErrorKind.SERVER_ERROR

    return ErrorKind.OTHER


def _sanitize_error(msg: str) -> str:
    """Strip potential API keys/tokens from error messages."""
    import re
    msg = re.sub(r'(sk-[a-zA-Z0-9]{20,})', 'sk-***', msg)
    msg = re.sub(r'(key|token|secret|password)[\s=:]+\S+', r'\1=***', msg, flags=re.IGNORECASE)
    return msg[:500]


_RETRYABLE_STATUSES = frozenset({408, 409, 429})


def _should_retry_request(e: Exception) -> tuple[bool, float | None]:
    """Decide whether an API error is retryable, and any retry-after delay.

    Mirrors shouldRetry: retry on 408/409/429, any 5xx (incl. 529 overloaded),
    and connection/timeout errors. The previous substring-only check
    ('rate'+'limit') missed 500/502/503/529/overloaded entirely.
    """
    status = getattr(e, "status_code", None)
    if status is None:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)

    s = str(e).lower()
    should = False
    if isinstance(status, int) and (status in _RETRYABLE_STATUSES or status >= 500):
        should = True
    elif (
        "overloaded" in s
        or "529" in s
        or ("rate" in s and "limit" in s)
        or "connection" in s
        or "timeout" in s
        or "temporarily unavailable" in s
    ):
        should = True

    retry_after: float | None = None
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        ra = headers.get("retry-after")
        if ra:
            try:
                retry_after = float(ra)
            except (TypeError, ValueError):
                retry_after = None
    return should, retry_after


class LLMClient:
    """
    Multi-provider LLM client with retry logic.

    Detects provider from config/env:
      - ANTHROPIC_API_KEY → Anthropic
      - OPENAI_API_KEY → OpenAI
      - AZURE_OPENAI_API_KEY → Azure OpenAI
    """

    def __init__(self, config: Config) -> None:
        self._client = None
        self._max_retries = 10
        self._max_overload_before_fallback = 3
        self._fallback_active = False
        self.config = config
        self._provider = self._detect_provider()
        logger.info(f"LLM provider: {self._provider}")

    def _detect_provider(self) -> str:
        """Detect which LLM provider to use."""
        provider = os.environ.get("LLM_PROVIDER", "").lower()
        if provider in ("anthropic", "openai", "azure"):
            return provider

        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if os.environ.get("AZURE_OPENAI_API_KEY"):
            return "azure"

        return "azure"

    def _request_model(self) -> str:
        """Resolve the `model` field for the OpenAI/Azure request.

        Azure OpenAI routes by deployment name, which is decoupled from the
        underlying model id — a deployment can be named anything. Sending the
        config model name on Azure 404s as DeploymentNotFound. OpenAI routes by
        model id, so it keeps using the config model name.
        """
        if getattr(self, "_fallback_active", False) and self.config.fallback_model:
            return self.config.fallback_model
        if self._provider == "azure" and self.config.azure_deployment:
            return self.config.azure_deployment
        return self.config.model_name

    def _get_client(self):
        """Lazily create the appropriate client."""
        if self._client is not None:
            return self._client

        if self._provider == "anthropic":
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
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
        overload_count = 0
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
                should_retry, retry_after = _should_retry_request(e)

                s = str(e).lower()
                if "overloaded" in s or "529" in s or getattr(e, "status_code", None) == 529:
                    overload_count += 1
                    if (
                        overload_count >= self._max_overload_before_fallback
                        and self.config.fallback_model
                        and not self._fallback_active
                    ):
                        self._fallback_active = True
                        logger.warning(
                            f"Primary model overloaded {overload_count}x — "
                            f"switching to fallback model {self.config.fallback_model}"
                        )

                if should_retry and attempt < self._max_retries:
                    if retry_after is not None:
                        wait_time = retry_after
                    else:
                        import random
                        base = min(0.5 * (2 ** attempt), 32.0)
                        wait_time = base + random.random() * 0.25 * base
                    logger.warning(f"Retrying in {wait_time}s (attempt {attempt + 1}): {type(e).__name__}")
                    await asyncio.sleep(wait_time)
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


    async def _openai_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool = True,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream from OpenAI or Azure OpenAI."""
        client = self._get_client()
        messages = _strip_dynamic_boundary(messages)
        kwargs: dict[str, Any] = {
            "messages": messages,
            "model": self._request_model(),
            "stream": stream,
        }

        if stream:
            kwargs["stream_options"] = {"include_usage": True}

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
                    prompt_tokens=getattr(chunk.usage, "prompt_tokens", 0),
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
                        tool_calls[idx] = {"id": "", "name": "", "arguments": "", "started": False}

                    if tc_delta.id:
                        tool_calls[idx]["id"] = tc_delta.id

                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls[idx]["arguments"] += tc_delta.function.arguments

                    if (not tool_calls[idx]["started"]
                            and tool_calls[idx]["name"]
                            and tool_calls[idx]["id"]):
                        tool_calls[idx]["started"] = True
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_START,
                            tool_call_delta=ToolCallDelta(
                                call_id=tool_calls[idx]["id"],
                                name=tool_calls[idx]["name"],
                            ),
                        )

        import uuid as _uuid
        for idx, tc in tool_calls.items():
            if not tc["id"]:
                tc["id"] = f"call_{_uuid.uuid4().hex[:24]}"
            if not tc.get("started"):
                yield StreamEvent(
                    type=StreamEventType.TOOL_CALL_START,
                    tool_call_delta=ToolCallDelta(call_id=tc["id"], name=tc["name"]),
                )
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
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )

        yield StreamEvent(
            type=StreamEventType.MESSAGE_COMPLETE,
            finish_reason=choice.finish_reason,
            usage=token_usage,
        )


    async def _anthropic_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream from the Anthropic API."""
        client = self._get_client()

        system_content = ""
        oai_conversation = []
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                oai_conversation.append(_remap_image_blocks_for_anthropic(msg))
        conversation = _remap_conversation_for_anthropic(oai_conversation)

        anthropic_tools = []
        if tools:
            for tool in tools:
                anthropic_tools.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
                })

        conversation = _add_message_cache_breakpoint(conversation)

        max_out = getattr(self.config, "max_output_tokens", None) or 32_000
        anthropic_model = (
            self.config.fallback_model
            if self._fallback_active and self.config.fallback_model
            else self.config.model_name
        )
        kwargs: dict[str, Any] = {
            "model": anthropic_model,
            "max_tokens": max_out,
            "messages": conversation,
        }
        if system_content:
            kwargs["system"] = _build_anthropic_system_blocks(system_content)
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

            final = await stream.get_final_message()
            cache_read = getattr(final.usage, "cache_read_input_tokens", 0) or 0
            cache_creation = getattr(final.usage, "cache_creation_input_tokens", 0) or 0
            input_total = final.usage.input_tokens + cache_creation + cache_read
            usage = TokenUsage(
                prompt_tokens=input_total,
                completion_tokens=final.usage.output_tokens,
                total_tokens=input_total + final.usage.output_tokens,
                cached_tokens=cache_read,
                cache_creation_tokens=cache_creation,
            )

            yield StreamEvent(
                type=StreamEventType.MESSAGE_COMPLETE,
                finish_reason=final.stop_reason or "end_turn",
                usage=usage,
            )


def _build_anthropic_system_blocks(system_content: str) -> Any:
    """
    Split the assembled system prompt at DYNAMIC_BOUNDARY and emit cache_control
    on the static prefix block. Mirrors the cache-scope:'global' behavior.

    Returns:
      - list[dict] of text blocks when the marker is present
      - the raw string when no marker (legacy single-block path)

    Anthropic accepts both shapes for the `system` parameter; multi-block form
    is required for cache_control attachment.
    """
    if DYNAMIC_BOUNDARY not in system_content:
        return system_content

    prefix, _, suffix = system_content.partition(DYNAMIC_BOUNDARY)
    prefix = prefix.rstrip()
    suffix = suffix.lstrip()

    blocks: list[dict[str, Any]] = []
    if prefix:
        blocks.append({
            "type": "text",
            "text": prefix,
            "cache_control": {"type": "ephemeral"},
        })
    if suffix:
        blocks.append({"type": "text", "text": suffix})
    return blocks if blocks else system_content


def _add_message_cache_breakpoint(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Add cache_control to the last content block of the latest user message.

    Anthropic only honors up to 4 cache breakpoints per request. Combined with
    the system-prompt prefix and tools-array breakpoints, this gives the
    history-up-to-current-turn cache layer.

    The breakpoint goes on the LAST message regardless of role. Seeding it
    only on user messages loses the cache write whenever a turn ends on an
    assistant message.
    """
    if not messages:
        return messages
    out = [dict(m) for m in messages]
    last = out[-1]

    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [{
            "type": "text", "text": content,
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(content, list) and content:
        new_content = list(content)
        last_block = dict(new_content[-1])
        last_block["cache_control"] = {"type": "ephemeral"}
        new_content[-1] = last_block
        last["content"] = new_content
    return out


def _remap_conversation_for_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert an OpenAI-shaped conversation to Anthropic's message format.

    OpenAI: assistant {content, tool_calls:[{id,function:{name,arguments}}]},
            tool {role:"tool", tool_call_id, content}.
    Anthropic: assistant {content:[...text, {type:"tool_use",id,name,input}]},
               user {content:[{type:"tool_result",tool_use_id,content}]}.
    Consecutive tool results are merged into one user message (Anthropic
    requires all tool_results for a turn in a single user message).
    """
    import json as _json

    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            text = msg.get("content")
            if isinstance(text, str) and text.strip():
                blocks.append({"type": "text", "text": text})
            elif isinstance(text, list):
                blocks.extend(text)
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                try:
                    parsed = _json.loads(args) if isinstance(args, str) else (args or {})
                except (ValueError, TypeError):
                    parsed = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": parsed,
                })
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            tr_block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            }
            if (out and out[-1]["role"] == "user"
                    and isinstance(out[-1].get("content"), list)
                    and out[-1]["content"]
                    and isinstance(out[-1]["content"][0], dict)
                    and out[-1]["content"][0].get("type") == "tool_result"):
                out[-1]["content"].append(tr_block)
            else:
                out.append({"role": "user", "content": [tr_block]})
        else:
            out.append(msg)
    return out


def _remap_image_blocks_for_anthropic(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI-style multimodal content to Anthropic's block shape.

    Our context manager emits {"type":"image_url","image_url":{"url":"data:
    <media>;base64,<data>"}} (OpenAI form). Anthropic wants {"type":"image",
    "source":{"type":"base64","media_type":...,"data":...}}. Text blocks pass
    through. Non-list content is returned unchanged.
    """
    content = msg.get("content")
    if not isinstance(content, list):
        return msg
    new_blocks: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "image_url":
            url = block.get("image_url", {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(";base64,", 1)
                media_type = header[len("data:"):] or "image/png"
                new_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                })
        else:
            new_blocks.append(block)
    return {**msg, "content": new_blocks}


def _strip_dynamic_boundary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove the DYNAMIC_BOUNDARY sentinel from any system message before sending
    to a provider that doesn't honor it (OpenAI/Azure). Returns a new list;
    leaves the input untouched.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        if (
            msg.get("role") == "system"
            and isinstance(msg.get("content"), str)
            and DYNAMIC_BOUNDARY in msg["content"]
        ):
            cleaned = msg["content"].replace(DYNAMIC_BOUNDARY, "").strip()
            cleaned = cleaned.replace("\n\n\n\n", "\n\n")
            out.append({**msg, "content": cleaned})
        else:
            out.append(msg)
    return out
