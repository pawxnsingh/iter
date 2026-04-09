"""
Tool orchestration: Partition tools into parallel/serial batches.

Source: Claude Code src/services/tools/toolOrchestration.ts

The key insight: read-only tools can run in parallel safely,
but write tools must run one at a time.

Algorithm (exact from Claude Code):
  1. Walk tool calls in order
  2. Consecutive concurrency-safe tools → one batch (parallel)
  3. Each non-safe tool → its own batch (serial)
  4. Execute batches in order

Max concurrency: 10 (CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

from tools.base import Tool, ToolInvocation, ToolResult

logger = logging.getLogger(__name__)

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
    Claude Code: partitionToolCalls() in toolOrchestration.ts line 91.

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
                is_safe = False  # Conservative: treat errors as not-safe

        # Merge with previous batch if BOTH are concurrency-safe
        if is_safe and batches and batches[-1].is_concurrency_safe:
            batches[-1].calls.append(call)
        else:
            batches.append(Batch(is_concurrency_safe=is_safe, calls=[call]))

    return batches


async def execute_tool_call(
    call: ToolCallRequest,
    tool: Tool,
    cwd: Path,
) -> ToolCallResult:
    """Execute a single tool call."""
    invocation = ToolInvocation(params=call.arguments, cwd=cwd)

    try:
        # Validate params
        validation_errors = tool.validate_param(call.arguments)
        if validation_errors:
            result = ToolResult.error_result(
                f"Validation error: {'; '.join(validation_errors)}",
                metadata={"tool_name": call.name},
            )
        else:
            result = await tool.execute(invocation=invocation)

            # Enforce max result size with disk persistence (Claude Code: maxResultSizeChars + toolResultStorage)
            from services.tools.result_storage import maybe_persist_large_result
            if len(result.output) > tool.max_result_size_chars:
                result = ToolResult(
                    success=result.success,
                    output=maybe_persist_large_result(
                        output=result.output,
                        tool_name=call.name,
                        tool_use_id=call.call_id,
                        max_chars=tool.max_result_size_chars,
                    ),
                    error=result.error,
                    truncated=True,
                    metadata=result.metadata,
                    exit_code=result.exit_code,
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
) -> AsyncGenerator[ToolCallResult, None]:
    """
    Execute tool calls with concurrency control.
    Claude Code: runTools() in toolOrchestration.ts line 19.

    Read-only batches run in parallel (up to MAX_TOOL_USE_CONCURRENCY).
    Write batches run serially (one at a time).
    """
    batches = partition_tool_calls(calls, tools)

    for batch in batches:
        if batch.is_concurrency_safe and len(batch.calls) > 1:
            # Run read-only tools in PARALLEL
            logger.debug(
                f"Running {len(batch.calls)} tools in parallel: "
                f"{[c.name for c in batch.calls]}"
            )

            # Limit concurrency
            semaphore = asyncio.Semaphore(MAX_TOOL_USE_CONCURRENCY)

            async def run_with_semaphore(call: ToolCallRequest) -> ToolCallResult:
                async with semaphore:
                    tool = tools.get(call.name)
                    if not tool:
                        return ToolCallResult(
                            call_id=call.call_id,
                            name=call.name,
                            result=ToolResult.error_result(f"Unknown tool: {call.name}"),
                            arguments=call.arguments,
                        )
                    return await execute_tool_call(call, tool, cwd)

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
            # Run write tools SERIALLY
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
                yield await execute_tool_call(call, tool, cwd)
