import logging
from pathlib import Path
from typing import Any

from config.config import Config
from tools.base import Tool, ToolInvocation, ToolResult
from tools.builtin import get_all_builtin_tools
from tools.subagents import SubagentTool, get_default_subagent_definitions

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, config: Config):
        self._tools: dict[str, Any] = {}
        self._mcp_tools: dict[str, Any] = {}
        self.config = config

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.debug(f"replacing tool: {tool.name}")

        self._tools[tool.name] = tool
        logger.debug(f"tool got registered successfully: {tool.name}")

    def register_mcp_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        logger.debug(f"MCP tool got registered successfully: {tool.name}")

    def unregister(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True

        return False

    def get_tools(self) -> list[Tool]:
        tools = []
        for tool in self._tools.values():
            tools.append(tool)

        for mcp_tool in self._mcp_tools.values():
            tools.append(mcp_tool)

        if self.config.allowed_tools:
            allowed_set = set(self.config.allowed_tools)
            tools = [t for t in tools if t.name in allowed_set]

        return tools

    def get(
        self,
        name: str,
    ) -> Tool | None:
        if name in self._tools:
            return self._tools[name]

        if name in self._mcp_tools:
            return self._mcp_tools[name]

        return None

    async def invoke(
        self,
        name: str,
        params: dict[str, Any],
        cwd: Path,
    ) -> ToolResult:
        tool = self.get(name)

        if tool is None:
            return ToolResult.error_result(
                f"Unknown Tool Name: {name}",
                metadata={"tool_name": name},
            )

        validation_error = tool.validate_param(params=params)
        if validation_error:
            return ToolResult.error_result(
                "Invalidation Error",
                metadata={
                    "tool_name": name,
                    "validation_error": validation_error,
                },
            )

        invocation = ToolInvocation(params=params, cwd=cwd)
        try:
            result = await tool.execute(invocation=invocation)

        except Exception as e:
            logger.exception(f"Tool {name} Raised an unexpected error")
            result = ToolResult.error_result(
                f"Internal Error {str(e)}",
                metadata={"tool_name": name},
            )

        # Enforce max result size (Claude Code pattern: maxResultSizeChars per tool)
        if len(result.output) > tool.max_result_size_chars:
            result = ToolResult(
                success=result.success,
                output=result.output[:tool.max_result_size_chars] + "\n... [output truncated]",
                error=result.error,
                truncated=True,
                metadata=result.metadata,
                exit_code=result.exit_code,
            )

        return result

    def get_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self.get_tools()]


def create_default_registry(config: Config) -> ToolRegistry:
    tool_registry = ToolRegistry(config=config)

    for tool_class in get_all_builtin_tools():
        tool_registry.register(tool_class(config=config))

    for subagent_def in get_default_subagent_definitions():
        tool_registry.register(
            SubagentTool(
                config=config,
                definition=subagent_def,
            )
        )

    return tool_registry
