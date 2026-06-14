from typing import Any

from config.config import Config
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.mcp.client import MCPClient, MCPToolInfo

_MAX_MCP_DESCRIPTION_LENGTH = 2048


class MCPTool(Tool):
    def __init__(
        self,
        config: Config,
        client: MCPClient,
        tool_info: MCPToolInfo,
        name: str,
    ) -> None:
        super().__init__(config)
        self._tool_info = tool_info
        self._client = client
        self.name = name
        desc = self._tool_info.description or ""
        self.description = (
            desc[:_MAX_MCP_DESCRIPTION_LENGTH] + "… [truncated]"
            if len(desc) > _MAX_MCP_DESCRIPTION_LENGTH else desc
        )

    @property
    def schema(self) -> dict[str, Any]:
        input_schema = self._tool_info.input_schema or {}
        if input_schema:
            schema = dict(input_schema)
            schema.setdefault("type", "object")
            return schema
        return {"type": "object", "properties": {}, "required": []}

    def is_mutating(self, params) -> bool:
        return not self._tool_info.read_only

    def is_read_only(self, params: dict) -> bool:
        return False

    def is_concurrency_safe(self, params: dict) -> bool:
        return self._tool_info.read_only

    def is_destructive(self, params: dict) -> bool:
        return self._tool_info.destructive

    kind = ToolKind.MCP

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        try:
            result = await self._client.call_tool(
                self._tool_info.name,
                invocation.params,
            )
            output = result.get("output", "")
            is_error = result.get("is_error", False)
            images = result.get("images") or []

            if is_error:
                return ToolResult.error_result(output)

            return ToolResult.success_result(output, images=images)
        except Exception as e:
            return ToolResult.error_result(f"MCP tool failed: {e}")
