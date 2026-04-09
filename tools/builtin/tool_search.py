"""
ToolSearch — search for and discover available tools.

Source: Claude Code src/tools/ToolSearchTool/

When there are many tools (MCP servers, plugins), not all tool schemas
are sent to the model. ToolSearch lets the model discover tools by
keyword search. Found tools get their schemas loaded on demand.
"""


from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


class ToolSearchParams(BaseModel):
    query: str = Field(
        ...,
        description="Search query to find tools (keyword search)",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum results to return",
    )


class ToolSearchTool(Tool):
    name = "ToolSearch"
    description = (
        "Search for available tools by keyword. Use this when you need a tool "
        "that might not be in your current tool list. Returns matching tools "
        "with their descriptions."
    )
    kind = ToolKind.READ
    schema = ToolSearchParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ToolSearchParams(**invocation.params)
        query = params.query.lower()

        # Search through all registered tools
        # The tool registry is accessed via the config's tool list
        # For now, search against known tool descriptions
        from tools.builtin import get_all_builtin_tools

        matches = []
        for tool_cls in get_all_builtin_tools():
            tool = tool_cls(self.config)
            name = tool.name.lower()
            desc = tool.description.lower()

            # Simple keyword matching
            if query in name or query in desc:
                matches.append({
                    "name": tool.name,
                    "description": tool.description[:200],
                })

        if not matches:
            return ToolResult.success_result(
                f"No tools found matching '{params.query}'. "
                f"Try different keywords or check /tools for the full list."
            )

        lines = [f"Found {len(matches)} tool(s) matching '{params.query}':", ""]
        for m in matches[:params.max_results]:
            lines.append(f"  {m['name']}: {m['description']}")

        return ToolResult.success_result("\n".join(lines))
