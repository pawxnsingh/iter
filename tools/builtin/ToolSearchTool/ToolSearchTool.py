"""
ToolSearch — search for and discover available tools.

Source: Claude Code src/tools/ToolSearchTool/

When there are many tools (MCP servers, plugins), not all tool schemas
are sent to the model. ToolSearch lets the model discover tools by
keyword search. Found tools get their schemas loaded on demand.
"""


from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.ToolSearchTool.prompt import TOOL_SEARCH_TOOL_PROMPT


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
    description = "Fetches full schema definitions for deferred tools so they can be called."
    prompt = TOOL_SEARCH_TOOL_PROMPT
    kind = ToolKind.READ
    schema = ToolSearchParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ToolSearchParams(**invocation.params)
        from tools.builtin import get_all_builtin_tools

        all_tools = [tool_cls(self.config) for tool_cls in get_all_builtin_tools()]
        raw = params.query.strip()

        # "select:Read,Edit" → fetch those exact tools' full schemas (CC parity:
        # direct selection returns callable schema definitions).
        if raw.lower().startswith("select:"):
            wanted = {n.strip().lower() for n in raw[len("select:"):].split(",") if n.strip()}
            selected = [t for t in all_tools if t.name.lower() in wanted]
            if not selected:
                return ToolResult.error_result(
                    f"No tools matched select query: {raw[len('select:'):]}"
                )
            return ToolResult.success_result(self._format_schemas(selected))

        # keyword search → return matched tools' full schemas (CC yields the
        # matched tools' definitions, not just descriptions).
        query = raw.lower()
        matches = [
            t for t in all_tools
            if query in t.name.lower() or query in (t.description or "").lower()
        ]
        if not matches:
            return ToolResult.success_result(
                f"No tools found matching '{params.query}'. "
                f"Try different keywords or check /tools for the full list."
            )
        return ToolResult.success_result(self._format_schemas(matches[: params.max_results]))

    def _format_schemas(self, tools: list) -> str:
        """Return full schema definitions so the model can call the tools
        (CC parity: ToolSearch yields the matched tools' definitions)."""
        import json as _json

        blocks = []
        for t in tools:
            schema = t.to_openai_schema()
            blocks.append(_json.dumps({
                "name": schema["name"],
                "description": (schema.get("description") or "")[:500],
                "parameters": schema.get("parameters", {}),
            }, default=str))
        return "<functions>\n" + "\n".join(blocks) + "\n</functions>"
