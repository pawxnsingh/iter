"""
SendMessage tool — communicate with other agents/tasks.

Source: src/tools/SendMessageTool/

Used for multi-agent coordination. Sends a message to a running
agent or task by ID, continuing its conversation.
"""

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.SendMessageTool.prompt import (
    DESCRIPTION,
    SEND_MESSAGE_TOOL_PROMPT,
)


class SendMessageParams(BaseModel):
    to: str = Field(
        ...,
        description="Agent ID or task name to send the message to",
    )
    message: str = Field(
        ...,
        description="The message to send",
    )


class SendMessageTool(Tool):
    name = "SendMessage"
    description = DESCRIPTION
    prompt = SEND_MESSAGE_TOOL_PROMPT
    kind = ToolKind.WRITE
    schema = SendMessageParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = SendMessageParams(**invocation.params)
        return ToolResult.success_result(
            f"Message sent to '{params.to}': {params.message[:100]}..."
            if len(params.message) > 100
            else f"Message sent to '{params.to}': {params.message}"
        )
