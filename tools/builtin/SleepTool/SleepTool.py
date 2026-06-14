"""
Sleep tool — pause execution for a specified duration.

Source: src/tools/SleepTool/

Used by agents to:
  - Wait for background tasks to complete
  - Rate-limit API calls
  - Throttle rapid tool execution
"""

import asyncio

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.SleepTool.prompt import DESCRIPTION, SLEEP_TOOL_PROMPT


class SleepParams(BaseModel):
    seconds: int = Field(
        ...,
        ge=1,
        le=300,
        description="Number of seconds to sleep (1-300)",
    )
    reason: str = Field(
        default="",
        description="Why the agent is sleeping (for logging)",
    )


class SleepTool(Tool):
    name = "Sleep"
    description = DESCRIPTION
    prompt = SLEEP_TOOL_PROMPT
    kind = ToolKind.READ
    schema = SleepParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = SleepParams(**invocation.params)
        reason = f" ({params.reason})" if params.reason else ""
        await asyncio.sleep(params.seconds)
        return ToolResult.success_result(
            f"Slept for {params.seconds} seconds{reason}."
        )
