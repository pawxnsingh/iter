"""Compaction resume-message builders (single source of truth + partial-compact parity)."""
from services.compact.prompt import (
    get_compact_user_summary_message,
    get_partial_compact_prompt,
    get_compact_prompt,
)


def test_compact_user_summary_message_basic():
    msg = get_compact_user_summary_message("<summary>did stuff</summary>",
                                           suppress_follow_up_questions=True)
    assert "This session is being continued" in msg
    assert "Summary:" in msg
    assert "Continue the conversation" in msg


def test_compact_user_summary_message_with_transcript_and_preserved():
    msg = get_compact_user_summary_message(
        "<summary>x</summary>", suppress_follow_up_questions=False,
        transcript_path="/tmp/t.jsonl", recent_messages_preserved=True)
    assert "/tmp/t.jsonl" in msg
    assert "preserved verbatim" in msg
    assert "Continue the conversation" not in msg  # suppress=False


def test_partial_compact_prompt_directions():
    frm = get_partial_compact_prompt(direction="from")
    upto = get_partial_compact_prompt(direction="up_to")
    assert "RECENT portion" in frm
    assert "continuing session" in upto
    # both carry the no-tools guardrails
    for p in (frm, upto):
        assert "Do NOT call any tools" in p or "TEXT ONLY" in p


def test_compact_prompt_custom_instructions():
    p = get_compact_prompt("focus on the API changes")
    assert "focus on the API changes" in p
