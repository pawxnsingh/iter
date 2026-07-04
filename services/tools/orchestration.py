"""
Tool orchestration: Partition tools into parallel/serial batches.

The key insight: read-only tools can run in parallel safely,
but write tools must run one at a time.

Algorithm:
  1. Walk tool calls in order
  2. Consecutive concurrency-safe tools → one batch (parallel)
  3. Each non-safe tool → its own batch (serial)
  4. Execute batches in order

Max concurrency: 10
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

from tools.base import Tool, ToolInvocation, ToolResult, truncate_tool_result
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_TOOL_USE_CONCURRENCY = int(os.environ.get("MAX_TOOL_USE_CONCURRENCY", "10"))


@dataclass
class ToolCallRequest:
    """A single tool call from the model."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolCallResult:
    """Result from executing a tool call."""

    call_id: str
    name: str
    result: ToolResult
    arguments: dict[str, Any]


@dataclass
class Batch:
    """A group of tool calls that execute together."""

    is_concurrency_safe: bool
    calls: list[ToolCallRequest] = field(default_factory=list)


def partition_tool_calls(
    calls: list[ToolCallRequest],
    tools: dict[str, Tool],
) -> list[Batch]:
    """
    Partition tool calls into batches.

    Consecutive concurrency-safe tools → one batch (run in parallel)
    Each non-safe tool → its own batch (run serial)
    """
    batches: list[Batch] = []

    for call in calls:
        tool = tools.get(call.name)
        is_safe = False
        if tool:
            try:
                is_safe = tool.is_concurrency_safe(call.arguments)
            except Exception:
                is_safe = False

        if is_safe and batches and batches[-1].is_concurrency_safe:
            batches[-1].calls.append(call)
        else:
            batches.append(Batch(is_concurrency_safe=is_safe, calls=[call]))

    return batches


async def execute_tool_call(
    call: ToolCallRequest,
    tool: Tool,
    cwd: Path,
    abort_controller: Any = None,
    session_dir: Path | None = None,
) -> ToolCallResult:
    """Execute a single tool call."""
    if abort_controller is not None and getattr(abort_controller, "aborted", False):
        return ToolCallResult(
            call_id=call.call_id,
            name=call.name,
            result=ToolResult.error_result("Tool execution interrupted by user"),
            arguments=call.arguments,
        )
    invocation = ToolInvocation(
        params=call.arguments, cwd=cwd, abort_controller=abort_controller
    )

    try:
        validation_errors = tool.validate_param(call.arguments)
        if validation_errors:
            result = ToolResult.error_result(
                f"Validation error: {'; '.join(validation_errors)}",
                metadata={"tool_name": call.name},
            )
        else:
            result = await tool.execute(invocation=invocation)
            result = truncate_tool_result(
                result, tool, call_id=call.call_id, session_dir=session_dir
            )

    except Exception as e:
        logger.exception(f"Tool {call.name} raised unexpected error")
        result = ToolResult.error_result(
            f"Internal error: {e}",
            metadata={"tool_name": call.name},
        )

    return ToolCallResult(
        call_id=call.call_id,
        name=call.name,
        result=result,
        arguments=call.arguments,
    )


async def run_tools(
    calls: list[ToolCallRequest],
    tools: dict[str, Tool],
    cwd: Path,
    abort_controller: Any = None,
    session_dir: Path | None = None,
) -> AsyncGenerator[ToolCallResult, None]:
    """
    Execute tool calls with concurrency control.

    Read-only batches run in parallel (up to MAX_TOOL_USE_CONCURRENCY).
    Write batches run serially (one at a time).
    """
    batches = partition_tool_calls(calls, tools)

    def _aborted() -> bool:
        return abort_controller is not None and getattr(
            abort_controller, "aborted", False
        )

    for batch in batches:
        if _aborted():
            for call in batch.calls:
                yield ToolCallResult(
                    call_id=call.call_id,
                    name=call.name,
                    result=ToolResult.error_result(
                        "Tool execution interrupted by user"
                    ),
                    arguments=call.arguments,
                )
            continue
        if batch.is_concurrency_safe and len(batch.calls) > 1:
            logger.debug(
                f"Running {len(batch.calls)} tools in parallel: "
                f"{[c.name for c in batch.calls]}"
            )

            semaphore = asyncio.Semaphore(MAX_TOOL_USE_CONCURRENCY)

            async def run_with_semaphore(call: ToolCallRequest) -> ToolCallResult:
                async with semaphore:
                    tool = tools.get(call.name)
                    if not tool:
                        return ToolCallResult(
                            call_id=call.call_id,
                            name=call.name,
                            result=ToolResult.error_result(
                                f"Unknown tool: {call.name}"
                            ),
                            arguments=call.arguments,
                        )
                    return await execute_tool_call(
                        call, tool, cwd, abort_controller, session_dir
                    )

            tasks = [run_with_semaphore(call) for call in batch.calls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    yield ToolCallResult(
                        call_id=batch.calls[i].call_id,
                        name=batch.calls[i].name,
                        result=ToolResult.error_result(f"Execution error: {result}"),
                        arguments=batch.calls[i].arguments,
                    )
                else:
                    yield result
        else:
            for call in batch.calls:
                tool = tools.get(call.name)
                if not tool:
                    yield ToolCallResult(
                        call_id=call.call_id,
                        name=call.name,
                        result=ToolResult.error_result(f"Unknown tool: {call.name}"),
                        arguments=call.arguments,
                    )
                    continue

                logger.debug(f"Running tool serially: {call.name}")
                yield await execute_tool_call(
                    call, tool, cwd, abort_controller, session_dir
                )
