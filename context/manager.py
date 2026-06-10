"""
Context Manager: Manages conversation history with compaction.

This is the central message store. The query loop calls
get_messages_with_compaction() before every API call, which runs the full
compaction pipeline (microcompact → collapse → autocompact → tool-result
budget) and returns the API message list.

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
    calculate_token_warning_state,
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
    # Image attachments (base64) for multimodal user messages. Each entry:
    # {"media_type": str, "data": base64-str}. When present, to_dict() emits
    # OpenAI-style multimodal content (text + image_url blocks).
    images: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role}

        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id

        if self.tool_calls:
            result["tool_calls"] = self.tool_calls

        if self.images:
            # Multimodal content: text part (if any) + image_url blocks. The
            # Anthropic provider path remaps image_url → image blocks.
            parts: list[dict[str, Any]] = []
            if self.content:
                parts.append({"type": "text", "text": self.content})
            for img in self.images:
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img['media_type']};base64,{img['data']}"
                    },
                })
            result["content"] = parts
        elif self.content:
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

    def set_system_prompt(self, prompt: str) -> None:
        """Override the assembled system prompt.

        Used when spawning a subagent: the agent's specialized instructions
        (Explore's read-only rules, Plan's planning rules, etc.) are its system
        prompt, appended to the base prompt by AgentTool.
        """
        self._system_prompt = prompt

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

    def add_image_message(self, images: list[dict[str, str]], content: str = "") -> None:
        """Append a user message carrying image attachments.

        OpenAI/Azure accept image blocks only in user messages (not tool
        messages), so after a tool returns an image we surface it as a follow-up
        user message. Mirrors CC's image tool_result, adapted to the OpenAI
        message shape our provider uses.
        """
        item = MessageItem(
            role="user",
            content=content,
            images=images,
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
        Run the compaction pipeline and return the API message list.

        Phase order matches Claude Code (query.ts ~396-468):

          1. snip                  — lossless removal of oldest message groups
          2. microcompact          — clear stale tool results (zero cost)
          3. context_collapse      — merge adjacent same-role + collapse old
                                     tool calls. Runs BEFORE autocompact so
                                     that if collapse frees enough, autocompact
                                     becomes a no-op and we keep granular
                                     context instead of a lossy summary.
          4. autocompact           — LLM summarisation if still over threshold.
          5. apply_tool_result_budget — cap aggregate tool result bytes.

        Called before every LLM API call.
        """
        # Lazy imports: snip_compact and context_collapse both import
        # MessageItem from this module, so top-level imports here would
        # be a cycle.
        from services.compact.context_collapse import apply_context_collapse
        from services.compact.snip_compact import snip_compact_if_needed

        context_window = self.config.model.context_window

        # snip — lossless drop of oldest message groups if way over threshold
        self._messages = snip_compact_if_needed(
            self._messages, context_window, self._model_name,
        )

        # microcompact — always runs, zero cost
        self._messages = micro_compact(self._messages)

        # context collapse — BEFORE autocompact, deliberate
        self._messages = apply_context_collapse(self._messages, context_window)

        # autocompact — runs if still over threshold
        if self._compact_fn:
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

        # tool result budget — cap aggregate tool result bytes
        self.apply_tool_result_budget()

        return self._build_api_messages()

    def get_messages(self) -> list[dict[str, Any]]:
        """
        Get messages WITHOUT running compaction (synchronous).
        Use get_messages_with_compaction() in the query loop instead.
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
        Get token usage statistics with warning states. Warning thresholds come
        from calculate_token_warning_state() (single source of truth, CC parity).
        """
        context_window = self.config.model.context_window
        current_tokens = estimate_messages_tokens(self._messages, self._model_name)
        threshold = get_auto_compact_threshold(context_window)
        warn = calculate_token_warning_state(current_tokens, context_window)

        percent_used = round((current_tokens / context_window) * 100, 1) if context_window > 0 else 0

        return {
            "current_tokens": current_tokens,
            "context_window": context_window,
            "compact_threshold": threshold,
            "percent_used": percent_used,
            "percent_left": warn.percent_left,
            "messages": len(self._messages),
            "compacted": self._compact_tracking.compacted if self._compact_tracking else False,
            "above_warning": warn.is_above_warning,
            "above_error": warn.is_above_error,
            "above_compact": warn.is_above_auto_compact,
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
