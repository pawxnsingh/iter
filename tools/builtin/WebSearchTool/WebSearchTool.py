from ddgs import DDGS
from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.WebSearchTool.prompt import WEB_SEARCH_PROMPT


class WebSearchParams(BaseModel):
    query: str = Field(..., min_length=2, description="The search query to use")
    allowed_domains: list[str] | None = Field(
        None, description="Only include search results from these domains"
    )
    blocked_domains: list[str] | None = Field(
        None, description="Never include search results from these domains"
    )
    max_results: int = Field(
        10, ge=1, le=20, description="Maximum results to return (default: 10)"
    )


class WebSearchTool(Tool):
    name = "WebSearch"
    description = "Allows Claude to search the web and use the results to inform responses"
    prompt = WEB_SEARCH_PROMPT
    kind = ToolKind.NETWORK
    schema = WebSearchParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = WebSearchParams(**invocation.params)

        try:
            results = DDGS().text(
                params.query,
                region="us-en",
                safesearch="off",
                timelimit="y",
                page=1,
                backend="auto",
            )
        except Exception as e:
            return ToolResult.error_result(f"Search failed: {e}")

        # Domain filtering (CC parity: allowed_domains / blocked_domains).
        results = self._filter_domains(
            results, params.allowed_domains, params.blocked_domains
        )
        results = results[: params.max_results]

        if not results:
            return ToolResult.success_result(
                f"No results found for: {params.query}",
                metadata={
                    "results": 0,
                },
            )

        output_lines = [f"Search results for: {params.query}"]

        for i, result in enumerate(results, start=1):
            output_lines.append(f"{i}. Title: {result['title']}")
            output_lines.append(f"   URL: {result['href']}")
            if result.get("body"):
                output_lines.append(f"   Snippet: {result['body']}")

            output_lines.append("")

        return ToolResult.success_result(
            "\n".join(output_lines),
            metadata={
                "results": len(results),
            },
        )

    @staticmethod
    def _filter_domains(results, allowed, blocked):
        """Apply allowed/blocked domain filters (CC parity). A result's URL
        host must be in `allowed` (if set) and not in `blocked`. Domain match
        is suffix-based so 'example.com' matches 'www.example.com'."""
        from urllib.parse import urlparse

        if not allowed and not blocked:
            return results

        def host_of(r):
            return (urlparse(r.get("href", "")).hostname or "").lower()

        def matches(host, domains):
            for d in domains:
                d = d.lower().lstrip(".")
                if host == d or host.endswith("." + d):
                    return True
            return False

        out = []
        for r in results:
            host = host_of(r)
            if allowed and not matches(host, allowed):
                continue
            if blocked and matches(host, blocked):
                continue
            out.append(r)
        return out
