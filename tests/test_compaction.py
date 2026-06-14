"""Tests for context compaction system."""

from context.manager import MessageItem
from services.compact.auto_compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    AutoCompactTracking,
    get_auto_compact_threshold,
    should_auto_compact,
)
from services.compact.micro_compact import micro_compact
from services.compact.prompt import format_compact_summary, get_compact_prompt


def test_micro_compact_is_noop_keeps_old_tool_results():
    messages = [
        MessageItem(role="user", content="read file"),
        MessageItem(role="assistant", content="reading", tool_calls=[
            {"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}
        ]),
        MessageItem(role="tool", content="x" * 5000, tool_call_id="tc1"),
        MessageItem(role="assistant", content="done"),
    ]
    result = micro_compact(messages)
    assert result[2].content == "x" * 5000
    assert [m.content for m in result] == [m.content for m in messages]


def test_micro_compact_short_conversation():
    messages = [
        MessageItem(role="user", content="hi"),
        MessageItem(role="assistant", content="hello"),
    ]
    result = micro_compact(messages)
    assert len(result) == 2


def test_threshold_200k():
    assert get_auto_compact_threshold(200_000) == 167_000


def test_threshold_128k():
    assert get_auto_compact_threshold(128_000) == 95_000


def test_constants():
    assert AUTOCOMPACT_BUFFER_TOKENS == 13_000
    assert MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES == 3


def test_circuit_breaker():
    tracking = AutoCompactTracking(consecutive_failures=3)
    big = [MessageItem(role="user", content="x" * 500_000, token_count=200_000)]
    assert not should_auto_compact(big, 200_000, tracking)


def test_under_threshold_no_compact():
    small = [MessageItem(role="user", content="hello", token_count=10)]
    assert not should_auto_compact(small, 200_000, None)


def test_compact_prompt_structure():
    prompt = get_compact_prompt()
    assert "CRITICAL: Respond with TEXT ONLY" in prompt
    assert "Primary Request and Intent" in prompt
    assert "REMINDER: Do NOT call any tools" in prompt


def test_format_summary_strips_analysis():
    raw = "<analysis>thinking here</analysis><summary>the actual summary</summary>"
    formatted = format_compact_summary(raw)
    assert "thinking here" not in formatted
    assert "the actual summary" in formatted
