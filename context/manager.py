"""
Context Manager: Manages conversation history with compaction.

This is the central message store. The query loop calls get_message()
before every API call, which triggers compaction if needed.

Claude Code equivalent: The message management spread across query.ts
(compaction orchestration) and the REPL's message array.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from config.config import Config
from prompts.system import get_system_prompt
from services.compact.auto_compact import (
    AutoCompactTracking,
    auto_compact_if_needed,
    estimate_messages_tokens,
    get_auto_compact_threshold,
)
from services.compact.micro_compact import micro_compact
from tools.base import Tool
from utils.text import count_tokens

logger = logging.getLogger(__name__)


@dataclass
class MessageItem:
    role: Literal["user", "system", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    token_count: int | None = None
    turn_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role}

        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id

        if self.tool_calls:
            result["tool_calls"] = self.tool_calls

        if self.content:
            result["content"] = self.content

        return result


class ContextManager:
    def __init__(
        self,
        config: Config,
        user_memory: str | None,
        tools: list[Tool] | None,
    ) -> None:
        self._system_prompt = get_system_prompt(config, user_memory, tools)
        self.config = config
        self._messages: list[MessageItem] = []
        self._model_name = self.config.model_name
        self._turn_number = 0

        # Compaction state (Claude Code: AutoCompactTrackingState)
        self._compact_tracking: AutoCompactTracking | None = None

        # Compact function — injected by the agent so we don't import LLM here
        self._compact_fn: Callable[[list[MessageItem], str], Awaitable[str]] | None = None

    def set_compact_fn(self, fn: Callable[[list[MessageItem], str], Awaitable[str]]) -> None:
        """Inject the compact function (calls LLM to summarize)."""
        self._compact_fn = fn

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def total_tokens(self) -> int:
        return estimate_messages_tokens(self._messages, self._model_name)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def set_turn(self, turn: int) -> None:
        self._turn_number = turn

    def add_user_message(self, content: str) -> None:
        item = MessageItem(
            role="user",
            content=content or "",
            token_count=count_tokens(content, model=self._model_name),
            turn_number=self._turn_number,
        )
        self._messages.append(item)

    def add_assistant_message(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None,
    ) -> None:
        item = MessageItem(
            role="assistant",
            content=content or "",
            token_count=count_tokens(content, model=self._model_name),
            tool_calls=tool_calls or [],
            turn_number=self._turn_number,
        )
        self._messages.append(item)

    def add_tool_result(
        self,
        tool_call_id: str,
        content: str,
    ) -> None:
        item = MessageItem(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            token_count=count_tokens(content, model=self._model_name),
            turn_number=self._turn_number,
        )
        self._messages.append(item)

    async def get_messages_with_compaction(self) -> list[dict[str, Any]]:
        """
        Get messages for API call, applying compaction first.

        This is the Claude Code pattern:
          1. Microcompact (cheap — clear old tool results)
          2. Autocompact (expensive — summarize if over threshold)
          3. Return system prompt + compacted messages

        Called before every LLM API call.
        """
        # Step 1: Microcompact (always runs, zero cost)
        self._messages = micro_compact(self._messages)

        # Step 2: Autocompact (runs if over threshold)
        if self._compact_fn:
            context_window = self.config.model.context_window
            result = await auto_compact_if_needed(
                messages=self._messages,
                system_prompt=self._system_prompt,
                context_window=context_window,
                tracking=self._compact_tracking,
                model=self._model_name,
                compact_fn=self._compact_fn,
            )

            if result:
                self._messages = result.messages
                self._compact_tracking = result.tracking
                if result.summary:
                    logger.info(
                        f"Context compacted: {result.pre_compact_tokens} -> "
                        f"{result.post_compact_tokens} tokens"
                    )

        return self._build_api_messages()

    def get_message(self) -> list[dict[str, Any]]:
        """
        Get messages WITHOUT compaction (synchronous).
        Use get_messages_with_compaction() in the query loop instead.
        This exists for backwards compatibility.
        """
        return self._build_api_messages()

    def _build_api_messages(self) -> list[dict[str, Any]]:
        """Build the final message list for the API call."""
        messages = [{"role": "system", "content": self._system_prompt}]
        for item in self._messages:
            messages.append(item.to_dict())
        return messages

    def clear(self) -> None:
        """Clear all messages (for /clear command)."""
        self._messages = []
        self._compact_tracking = None

    def get_token_stats(self) -> dict[str, Any]:
        """
        Get token usage statistics with warning states.
        Claude Code: calculateTokenWarningState() in autoCompact.ts
        """
        context_window = self.config.model.context_window
        current_tokens = estimate_messages_tokens(self._messages, self._model_name)
        threshold = get_auto_compact_threshold(context_window)

        # Warning thresholds (Claude Code constants)
        warning_threshold = threshold - 20_000
        error_threshold = threshold - 20_000

        percent_used = round((current_tokens / context_window) * 100, 1) if context_window > 0 else 0
        percent_left = max(0, round(((threshold - current_tokens) / threshold) * 100)) if threshold > 0 else 0

        return {
            "current_tokens": current_tokens,
            "context_window": context_window,
            "compact_threshold": threshold,
            "percent_used": percent_used,
            "percent_left": percent_left,
            "messages": len(self._messages),
            "compacted": self._compact_tracking.compacted if self._compact_tracking else False,
            "above_warning": current_tokens >= warning_threshold,
            "above_error": current_tokens >= error_threshold,
            "above_compact": current_tokens >= threshold,
        }

    def apply_tool_result_budget(self) -> None:
        """
        Cap aggregate tool result size per turn.
        Claude Code: applyToolResultBudget() — MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200,000
        """
        MAX_AGGREGATE = 200_000
        tool_messages = [m for m in self._messages if m.role == "tool"]

        total = sum(len(m.content) for m in tool_messages)
        if total <= MAX_AGGREGATE:
            return

        # Sort by size (largest first) and truncate until under budget
        sorted_tools = sorted(tool_messages, key=lambda m: len(m.content), reverse=True)
        excess = total - MAX_AGGREGATE

        for msg in sorted_tools:
            if excess <= 0:
                break
            if len(msg.content) > 10_000:  # Only truncate large results
                old_len = len(msg.content)
                msg.content = msg.content[:5000] + "\n... [result truncated for context budget]"
                excess -= (old_len - len(msg.content))
