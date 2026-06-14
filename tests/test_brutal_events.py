"""
Brutal tests for agent/event.py — every factory method, every event type.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_event_type_values() -> None:
    from agent.event import AgentEventType

    assert AgentEventType.AGENT_START == "agent_start"
    assert AgentEventType.AGENT_END == "agent_end"
    assert AgentEventType.AGENT_ERROR == "agent_error"
    assert AgentEventType.TEXT_DELTA == "text_delta"
    assert AgentEventType.TEXT_COMPLETE == "text_complete"
    assert AgentEventType.TOOL_CALL_START == "tool_call_start"
    assert AgentEventType.TOOL_CALL_COMPLETE == "tool_call_complete"


def test_agent_start_factory() -> None:
    from agent.event import AgentEvent, AgentEventType

    e = AgentEvent.agent_start("hi")
    assert e.type == AgentEventType.AGENT_START
    assert e.data == {"message": "hi"}


def test_agent_end_factory_with_defaults() -> None:
    from agent.event import AgentEvent, AgentEventType

    e = AgentEvent.agent_end()
    assert e.type == AgentEventType.AGENT_END
    assert e.data["response"] is None
    assert e.data["usage"] is None


def test_agent_end_factory_with_usage() -> None:
    from agent.event import AgentEvent
    from client.response import TokenUsage

    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    e = AgentEvent.agent_end(response="ok", usage=usage)
    assert e.data["response"] == "ok"
    assert isinstance(e.data["usage"], dict)
    assert e.data["usage"].get("prompt_tokens") == 10
    assert e.data["usage"].get("completion_tokens") == 5
    assert e.data["usage"].get("total_tokens") == 15


def test_agent_error_factory() -> None:
    from agent.event import AgentEvent, AgentEventType

    e = AgentEvent.agent_error("rate limit", details={"retry_after": 5})
    assert e.type == AgentEventType.AGENT_ERROR
    assert e.data["error"] == "rate limit"
    assert e.data["details"]["retry_after"] == 5


def test_agent_error_factory_no_details() -> None:
    from agent.event import AgentEvent

    e = AgentEvent.agent_error("oops")
    assert e.data["error"] == "oops"
    assert e.data["details"] is None


def test_text_delta_factory() -> None:
    from agent.event import AgentEvent, AgentEventType

    e = AgentEvent.text_delta("hello ")
    assert e.type == AgentEventType.TEXT_DELTA
    assert e.data == {"content": "hello "}


def test_text_delta_empty_string() -> None:
    from agent.event import AgentEvent

    e = AgentEvent.text_delta("")
    assert e.data == {"content": ""}


def test_text_complete_factory() -> None:
    from agent.event import AgentEvent, AgentEventType

    e = AgentEvent.text_complete("final text")
    assert e.type == AgentEventType.TEXT_COMPLETE
    assert e.data["content"] == "final text"


def test_tool_call_start_factory_shape() -> None:
    from agent.event import AgentEvent, AgentEventType

    e = AgentEvent.tool_call_start("id1", "Read", {"path": "/tmp/x"})
    assert e.type == AgentEventType.TOOL_CALL_START
    assert e.data == {
        "call_id": "id1",
        "name": "Read",
        "arguments": {"path": "/tmp/x"},
    }


def test_tool_call_complete_factory_success() -> None:
    from agent.event import AgentEvent, AgentEventType
    from tools.base import ToolResult

    result = ToolResult.success_result(
        output="ok",
        metadata={"x": 1},
        exit_code=0,
    )
    e = AgentEvent.tool_call_complete("id2", "Bash", result)
    assert e.type == AgentEventType.TOOL_CALL_COMPLETE
    assert e.data["call_id"] == "id2"
    assert e.data["name"] == "Bash"
    assert e.data["success"] is True
    assert e.data["output"] == "ok"
    assert e.data["error"] is None
    assert e.data["diff"] is None
    assert e.data["exit_code"] == 0


def test_tool_call_complete_factory_error_with_diff() -> None:
    from agent.event import AgentEvent
    from tools.base import FileDiff, ToolResult

    diff = FileDiff(
        path=Path("/tmp/x"),
        old_content="old\n",
        new_content="new\n",
    )
    result = ToolResult(
        success=False,
        output="",
        error="bad",
        diff=diff,  # type: ignore[arg-type]
        truncated=True,
        exit_code=1,
    )
    e = AgentEvent.tool_call_complete("id3", "Edit", result)
    assert e.data["success"] is False
    assert e.data["error"] == "bad"
    assert e.data["truncated"] is True
    assert isinstance(e.data["diff"], str)
    assert "---" in e.data["diff"] and "+++" in e.data["diff"]


def test_agent_event_data_default_factory_independence() -> None:
    """Each AgentEvent should have its own dict — not shared."""
    from agent.event import AgentEvent, AgentEventType

    a = AgentEvent(type=AgentEventType.AGENT_START)
    b = AgentEvent(type=AgentEventType.AGENT_END)
    a.data["x"] = 1
    assert "x" not in b.data
