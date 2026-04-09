"""
Streaming Tool Executor — tools execute DURING model streaming.

Source: Claude Code src/services/tools/StreamingToolExecutor.ts

Instead of waiting for the model to finish, we start tool execution
as soon as a tool_use block arrives in the stream. This hides latency.

Tool lifecycle: queued → executing → completed → yielded

Concurrency rules (exact from Claude Code):
  - Can execute if: no tools running
  - OR: this tool is safe AND all running tools are safe
  - Non-concurrent tools block queue processing
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

from services.tools.orchestration import (
    ToolCallRequest,
    ToolCallResult,
    execute_tool_call,
)
from tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class ToolStatus(str, Enum):
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    YIELDED = "yielded"


@dataclass
class TrackedTool:
    """A tool being tracked through the streaming executor."""
    call: ToolCallRequest
    status: ToolStatus = ToolStatus.QUEUED
    is_concurrency_safe: bool = False
    result: ToolCallResult | None = None
    task: asyncio.Task | None = None


class StreamingToolExecutor:
    """
    Executes tools as they stream in with concurrency control.
    Claude Code: StreamingToolExecutor in StreamingToolExecutor.ts

    Key behaviors:
    - Tools start executing as soon as their tool_use block arrives
    - Concurrent-safe tools run in parallel
    - Non-concurrent tools run alone (exclusive)
    - Results are buffered and yielded IN ORDER (not completion order)
    """

    def __init__(self, tools: dict[str, Tool]):
        self.tools = tools
        self.tracked: list[TrackedTool] = []
        self._has_errored = False

    def add_tool(self, call: ToolCallRequest, cwd) -> None:
        """
        Add a tool to the execution queue.
        Called as each tool_use block arrives during streaming.
        Claude Code: addTool() in StreamingToolExecutor.ts
        """
        tool = self.tools.get(call.name)
        is_safe = False
        if tool:
            try:
                is_safe = tool.is_concurrency_safe(call.arguments)
            except Exception:
                is_safe = False

        tracked = TrackedTool(
            call=call,
            is_concurrency_safe=is_safe,
        )
        self.tracked.append(tracked)

        # Start processing immediately
        asyncio.create_task(self._process_queue(cwd))

    def _can_execute(self, is_safe: bool) -> bool:
        """
        Check if a tool can execute based on current concurrency state.
        Claude Code: canExecuteTool() in StreamingToolExecutor.ts line 129
        """
        executing = [t for t in self.tracked if t.status == ToolStatus.EXECUTING]
        return (
            len(executing) == 0
            or (is_safe and all(t.is_concurrency_safe for t in executing))
        )

    async def _process_queue(self, cwd) -> None:
        """
        Process the queue, starting tools when concurrency allows.
        Claude Code: processQueue() in StreamingToolExecutor.ts line 140
        """
        for tracked in self.tracked:
            if tracked.status != ToolStatus.QUEUED:
                continue

            if self._can_execute(tracked.is_concurrency_safe):
                await self._execute(tracked, cwd)
            elif not tracked.is_concurrency_safe:
                # Non-concurrent tool blocks — stop checking further
                break

    async def _execute(self, tracked: TrackedTool, cwd) -> None:
        """Execute a single tracked tool."""
        tracked.status = ToolStatus.EXECUTING

        tool = self.tools.get(tracked.call.name)
        if not tool:
            tracked.result = ToolCallResult(
                call_id=tracked.call.call_id,
                name=tracked.call.name,
                result=ToolResult.error_result(f"Unknown tool: {tracked.call.name}"),
                arguments=tracked.call.arguments,
            )
            tracked.status = ToolStatus.COMPLETED
            return

        try:
            tracked.result = await execute_tool_call(tracked.call, tool, cwd)
        except Exception as e:
            tracked.result = ToolCallResult(
                call_id=tracked.call.call_id,
                name=tracked.call.name,
                result=ToolResult.error_result(f"Error: {e}"),
                arguments=tracked.call.arguments,
            )
            self._has_errored = True

        tracked.status = ToolStatus.COMPLETED

        # Wake up queue to start next tool
        asyncio.create_task(self._process_queue(cwd))

    async def get_results(self) -> list[ToolCallResult]:
        """
        Wait for all tools to complete, return results IN ORDER.
        Claude Code: getRemainingResults() yields in order.
        """
        results = []
        for tracked in self.tracked:
            # Wait for completion
            while tracked.status not in (ToolStatus.COMPLETED, ToolStatus.YIELDED):
                await asyncio.sleep(0.01)

            if tracked.status == ToolStatus.COMPLETED and tracked.result:
                results.append(tracked.result)
                tracked.status = ToolStatus.YIELDED

        return results
