"""
Context Manager: Manages conversation history with compaction.

This is the central message store. The query loop calls
get_messages_with_compaction() before every API call, which runs the full
compaction pipeline (microcompact → collapse → autocompact → tool-result
budget) and returns the API message list.

(compaction orchestration) and the REPL's message array.
"""

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
from utils.logger import get_logger
from utils.text import count_tokens

logger = get_logger(__name__)

_SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"


def _ensure_tool_result_pairing(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ensureToolResultPairing: every assistant tool_call
    must be followed by a matching tool result before the request is sent. Any
    tool_call id with no tool message anywhere gets a synthetic error result
    injected right after its assistant message — so an interrupted/mid-turn state
    never sends an orphan tool_call (which the API rejects with a 400)."""
    answered = {
        m.get("tool_call_id")
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    out: list[dict[str, Any]] = []
    for m in messages:
        out.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tcid = tc.get("id")
                if tcid and tcid not in answered:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": tcid,
                            "content": _SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                        }
                    )
                    answered.add(tcid)
    return out


@dataclass
class MessageItem:
    role: Literal["user", "system", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    token_count: int | None = None
    turn_number: int = 0
    images: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role}

        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id

        if self.tool_calls:
            result["tool_calls"] = self.tool_calls

        if self.images:
            parts: list[dict[str, Any]] = []
            if self.content:
                parts.append({"type": "text", "text": self.content})
            for img in self.images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img['media_type']};base64,{img['data']}"
                        },
                    }
                )
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
        mcp_instructions: list[tuple[str, str]] | None = None,
    ) -> None:
        self._system_prompt = get_system_prompt(
            config, user_memory, tools, mcp_instructions
        )
        self.config = config
        self._messages: list[MessageItem] = []
        self._model_name = self.config.model_name
        self._turn_number = 0

        self._compact_tracking: AutoCompactTracking | None = None

        self._compact_fn: Callable[[list[MessageItem], str], Awaitable[str]] | None = (
            None
        )
        self._summary_sink: Callable[[str], None] | None = None
        self._transcript_path: str | None = None
        self._user_context_reminder: str | None = None
        self._system_context_suffix: str | None = None

    def set_user_context_reminder(self, text: str | None) -> None:
        """Set the turn-1 <system-reminder> user-context message, prepended at
        build time."""
        self._user_context_reminder = text or None

    def set_system_context_suffix(self, text: str | None) -> None:
        """Set the gitStatus suffix appended to the system prompt (
        appendSystemContext)."""
        self._system_context_suffix = text or None

    def set_compact_fn(
        self, fn: Callable[[list[MessageItem], str], Awaitable[str]]
    ) -> None:
        """Inject the compact function (calls LLM to summarize)."""
        self._compact_fn = fn

    def set_summary_sink(self, fn: Callable[[str], None]) -> None:
        """Inject a callback to persist a compaction summary to the transcript."""
        self._summary_sink = fn

    def set_transcript_path(self, path: str | None) -> None:
        """Transcript path for the post-compact summary's read-back hint."""
        self._transcript_path = path or None

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

    def add_image_message(
        self, images: list[dict[str, str]], content: str = ""
    ) -> None:
        """Append a user message carrying image attachments.

        OpenAI/Azure accept image blocks only in user messages (not tool
        messages), so after a tool returns an image we surface it as a follow-up
        user message. Mirrors the image tool_result, adapted to the OpenAI
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

        Phase order matches

          1. apply_tool_result_budget — cap tool result bytes FIRST, so every
                                     downstream token count reflects the budgeted
                                     sizes. Running
                                     it last would let autocompact fire an
                                     expensive LLM summary on inflated counts that
                                     the budget pass would have made unnecessary.
          2. snip                  — lossless removal of oldest message groups
          3. context_collapse      — merge adjacent same-role + collapse old
                                     tool calls. Runs BEFORE autocompact so
                                     that if collapse frees enough, autocompact
                                     becomes a no-op and we keep granular
                                     context instead of a lossy summary.
          4. autocompact           — LLM summarisation if still over threshold.

        NOTE: microcompact is intentionally a no-op — its legacy unconditional
        tool-result clearing destroyed context; autocompact handles pressure.
        See services/compact/micro_compact.py.

        Called before every LLM API call.
        """
        from services.compact.context_collapse import apply_context_collapse
        from services.compact.snip_compact import snip_compact_if_needed

        context_window = self.config.model.context_window

        self.apply_tool_result_budget()

        self._messages = snip_compact_if_needed(
            self._messages,
            context_window,
            self._model_name,
        )

        self._messages = micro_compact(self._messages)

        self._messages = apply_context_collapse(self._messages, context_window)

        if self._compact_fn:
            result = await auto_compact_if_needed(
                messages=self._messages,
                system_prompt=self._system_prompt,
                context_window=context_window,
                tracking=self._compact_tracking,
                model=self._model_name,
                compact_fn=self._compact_fn,
                transcript_path=self._transcript_path,
            )

            if result:
                self._messages = result.messages
                self._compact_tracking = result.tracking
                if result.summary:
                    logger.info(
                        f"Context compacted: {result.pre_compact_tokens} -> "
                        f"{result.post_compact_tokens} tokens"
                    )
                    if self._summary_sink:
                        try:
                            from services.compact.prompt import format_compact_summary

                            self._summary_sink(format_compact_summary(result.summary))
                        except Exception:
                            logger.warning(
                                "Failed to persist compaction summary", exc_info=True
                            )

        return self._build_api_messages()

    def get_messages(self) -> list[dict[str, Any]]:
        """
        Get messages WITHOUT running compaction (synchronous).
        Use get_messages_with_compaction() in the query loop instead.
        """
        return self._build_api_messages()

    def _build_api_messages(self) -> list[dict[str, Any]]:
        """Build the final message list for the API call.

        The turn-1 user-context reminder (agentMd/currentDate) is prepended
        HERE — at every build, after the system prompt and before the
        conversation. Doing it at build time (not storing it) means it survives
        compaction and is always present on a resumed session.
        """
        system_content = self._system_prompt
        if self._system_context_suffix:
            system_content = f"{system_content}\n\n{self._system_context_suffix}"
        messages = [{"role": "system", "content": system_content}]
        if self._user_context_reminder:
            messages.append({"role": "user", "content": self._user_context_reminder})
        for item in self._messages:
            messages.append(item.to_dict())
        return _ensure_tool_result_pairing(messages)

    def clear(self) -> None:
        """Clear all messages (for /clear command)."""
        self._messages = []
        self._compact_tracking = None

    def get_token_stats(self) -> dict[str, Any]:
        """
        Get token usage statistics with warning states. Warning thresholds come
        from calculate_token_warning_state.
        """
        context_window = self.config.model.context_window
        current_tokens = estimate_messages_tokens(self._messages, self._model_name)
        threshold = get_auto_compact_threshold(context_window)
        warn = calculate_token_warning_state(current_tokens, context_window)

        percent_used = (
            round((current_tokens / context_window) * 100, 1)
            if context_window > 0
            else 0
        )

        return {
            "current_tokens": current_tokens,
            "context_window": context_window,
            "compact_threshold": threshold,
            "percent_used": percent_used,
            "percent_left": warn.percent_left,
            "messages": len(self._messages),
            "compacted": self._compact_tracking.compacted
            if self._compact_tracking
            else False,
            "above_warning": warn.is_above_warning,
            "above_error": warn.is_above_error,
            "above_compact": warn.is_above_auto_compact,
        }

    def apply_tool_result_budget(self) -> None:
        """
        Cap EACH tool result message independently — the enforceToolResultBudget
        is PER-MESSAGE, not aggregate (MAX_TOOL_RESULTS_PER_MESSAGE_CHARS =
        200,000). Two 150K results in different messages are both under budget
        and untouched; does NOT sum across messages and truncate the largest.

        persists an over-budget result to disk and leaves a read-it-back
        preview; lacking that subsystem, we truncate a single over-budget message
        with a clear marker. (Per-tool result-size caps — Bash 30K, Grep 20K,
        default 100K — already keep individual results well under 200K, so this
        rarely fires; the point is to stop the destructive cross-message
        aggregate truncation that diverged from.)
        """
        MAX_PER_MESSAGE = 200_000
        for msg in self._messages:
            if msg.role == "tool" and len(msg.content) > MAX_PER_MESSAGE:
                over = len(msg.content) - MAX_PER_MESSAGE
                msg.content = (
                    msg.content[:MAX_PER_MESSAGE]
                    + f"\n... [tool result truncated: {over} chars over the "
                    f"{MAX_PER_MESSAGE}-char per-result budget]"
                )
