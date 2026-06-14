"""Anthropic provider message remap: OpenAI tool_calls/tool → Anthropic tool_use/tool_result.

Without this, multi-turn tool-using conversations are malformed for the
Anthropic provider (it only ever worked on Azure/OpenAI).
"""
from client.llm_client import (
    _remap_conversation_for_anthropic,
    _remap_image_blocks_for_anthropic,
)


def test_tool_calls_become_tool_use_blocks():
    conv = [
        {"role": "user", "content": "read a and b"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": '{"file_path":"/a"}'}},
            {"id": "c2", "type": "function", "function": {"name": "Read", "arguments": '{"file_path":"/b"}'}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "AAA"},
        {"role": "tool", "tool_call_id": "c2", "content": "BBB"},
        {"role": "assistant", "content": "Both read."},
    ]
    out = _remap_conversation_for_anthropic(conv)

    asst = out[1]
    assert asst["role"] == "assistant"
    tool_uses = [b for b in asst["content"] if b["type"] == "tool_use"]
    assert len(tool_uses) == 2
    assert tool_uses[0]["input"] == {"file_path": "/a"}
    assert tool_uses[0]["id"] == "c1" and tool_uses[0]["name"] == "Read"

    tr = out[2]
    assert tr["role"] == "user"
    results = tr["content"]
    assert len(results) == 2 and all(b["type"] == "tool_result" for b in results)
    assert {b["tool_use_id"] for b in results} == {"c1", "c2"}

    assert out[3] == {"role": "assistant", "content": "Both read."}


def test_plain_messages_passthrough():
    conv = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert _remap_conversation_for_anthropic(conv) == conv


def test_image_url_remap_to_anthropic_block():
    msg = {"role": "user", "content": [
        {"type": "text", "text": "x"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]}
    out = _remap_image_blocks_for_anthropic(msg)
    img = next(b for b in out["content"] if b["type"] == "image")
    assert img["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}
