"""
SendMessage tool — communicate with other agents/tasks.

Source: Claude Code src/tools/SendMessageTool/

Used for multi-agent coordination. Sends a message to a running
agent or task by ID, continuing its conversation.
"""

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


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
    description = (
        "Send a message to a running agent or background task. "
        "Use the agent's ID or name as the 'to' field. "
        "The agent will receive the message and can continue its work."
    )
    kind = ToolKind.WRITE
    schema = SendMessageParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = SendMessageParams(**invocation.params)
        # In a full implementation, this would route to a running agent/task
        # For now, return a placeholder
        return ToolResult.success_result(
            f"Message sent to '{params.to}': {params.message[:100]}..."
            if len(params.message) > 100
            else f"Message sent to '{params.to}': {params.message}"
        )
