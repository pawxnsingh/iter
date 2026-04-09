from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


class WebFetchParams(BaseModel):
    url: str = Field(..., description="URL to fetch (must be http:// or https://)")
    timeout: int = Field(
        30,
        ge=5,
        le=120,
        description="Request timeout in seconds (default: 120)",
    )


class WebFetchTool(Tool):
    name = "WebFetch"
    description = "Fetches content from a specified URL. Returns the response body as text. The URL must be fully-formed and valid (http:// or https://)."
    kind = ToolKind.NETWORK
    schema = WebFetchParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = WebFetchParams(**invocation.params)

        parsed = urlparse(params.url)
        if not parsed.scheme or parsed.scheme not in ("http", "https"):
            return ToolResult.error_result("Url must be http:// or https://")

        # SSRF protection: block internal/private network addresses
        hostname = parsed.hostname or ""
        if hostname:
            import ipaddress
            import socket
            blocked_keywords = {"localhost", "metadata.google.internal"}
            if hostname in blocked_keywords or hostname.startswith("169.254."):
                return ToolResult.error_result(f"Blocked: internal address {hostname}")
            try:
                resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for _, _, _, _, addr in resolved:
                    ip = ipaddress.ip_address(addr[0])
                    if ip.is_private or ip.is_loopback or ip.is_link_local:
                        return ToolResult.error_result(f"Blocked: resolved to internal IP {ip}")
            except (socket.gaierror, ValueError):
                pass  # Let httpx handle DNS failures

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(params.timeout),
                follow_redirects=True,
            ) as client:
                response = await client.get(params.url)
                response.raise_for_status()
                text = response.text
        except httpx.HTTPStatusError as e:
            return ToolResult.error_result(
                f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
            )
        except Exception as e:
            return ToolResult.error_result(f"Request failed: {e}")

        if len(text) > 100 * 1024:
            text = text[: 100 * 1024] + "\n... [content truncated]"

        return ToolResult.success_result(
            text,
            metadata={
                "status_code": response.status_code,
                "content_length": len(response.content),
            },
        )
