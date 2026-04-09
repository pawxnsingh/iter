"""
Message types matching Claude Code's message hierarchy.

Claude Code source: src/types/message.ts

Message hierarchy:
  Message (base)
    -> UserMessage
    -> AssistantMessage
    -> SystemMessage
    -> ToolResultMessage
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


@dataclass
class Message:
    """Base message type. All messages in the conversation are this."""
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    uuid: str = field(default_factory=lambda: str(uuid4()))
    parent_uuid: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0

    # Tool-specific fields
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_name: str | None = None

    # Tracking
    turn_number: int = 0

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to dict for LLM API call."""
        result: dict[str, Any] = {"role": self.role}

        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id

        if self.tool_calls:
            result["tool_calls"] = self.tool_calls

        if self.content:
            result["content"] = self.content

        return result


@dataclass
class ToolUseBlock:
    """A tool use request from the model."""
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallInfo:
    """Parsed tool call from streaming response."""
    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


# Message creation helpers (mirrors Claude Code's utils/messages.ts)

def create_user_message(content: str, **kwargs) -> Message:
    return Message(role="user", content=content, **kwargs)


def create_assistant_message(
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    **kwargs,
) -> Message:
    return Message(role="assistant", content=content, tool_calls=tool_calls, **kwargs)


def create_tool_result_message(
    tool_call_id: str,
    content: str,
    tool_name: str | None = None,
    is_error: bool = False,
    **kwargs,
) -> Message:
    return Message(
        role="tool",
        content=content,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        metadata={"is_error": is_error, **kwargs.pop("metadata", {})},
        **kwargs,
    )


def create_system_message(content: str, **kwargs) -> Message:
    return Message(role="system", content=content, **kwargs)
