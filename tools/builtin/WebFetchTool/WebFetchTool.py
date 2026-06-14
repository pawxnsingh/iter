"""
WebFetch tool — fetch a URL and process its content with a prompt.

Source: contract:
takes `url` + `prompt`, fetches the page, converts HTML→text, then runs the
prompt against the content with a small fast model and returns the model's
answer (not raw HTML). We mirror that: fetch → strip HTML → one-shot LLM call.

SSRF protections (block localhost / link-local / private IPs) are kept.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from client.llm_client import LLMClient
from client.response import StreamEventType
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.WebFetchTool.prompt import WEB_FETCH_PROMPT

_MAX_CONTENT_CHARS = 100_000


class WebFetchParams(BaseModel):
    url: str = Field(..., description="The URL to fetch content from")
    prompt: str = Field(..., description="The prompt to run on the fetched content")
    timeout: int = Field(30, ge=5, le=120, description="Request timeout in seconds")


class WebFetchTool(Tool):
    name = "WebFetch"
    description = "Fetches content from a specified URL and processes it using an AI model"
    prompt = WEB_FETCH_PROMPT
    kind = ToolKind.NETWORK
    schema = WebFetchParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return True

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = WebFetchParams(**invocation.params)

        parsed = urlparse(params.url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult.error_result("Url must be http:// or https://")

        url_to_fetch = params.url
        if parsed.scheme == "http":
            url_to_fetch = parsed._replace(scheme="https").geturl()

        blocked = self._ssrf_block(parsed.hostname or "")
        if blocked:
            return ToolResult.error_result(blocked)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(params.timeout), follow_redirects=True
            ) as client:
                response = await client.get(url_to_fetch)
                response.raise_for_status()
                raw = response.text
                final_url = str(response.url)
        except httpx.HTTPStatusError as e:
            return ToolResult.error_result(
                f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
            )
        except Exception as e:
            return ToolResult.error_result(f"Request failed: {e}")

        if not self._is_permitted_redirect(url_to_fetch, final_url):
            return ToolResult.success_result(
                f"REDIRECT: {params.url} redirected to a different host:\n{final_url}\n"
                f"Make a new WebFetch request with this URL to fetch the content.",
                metadata={"redirect": final_url},
            )

        text = self._html_to_text(raw)
        if len(text) > _MAX_CONTENT_CHARS:
            text = text[:_MAX_CONTENT_CHARS] + "\n... [content truncated]"

        answer = await self._process_with_prompt(params.prompt, text, final_url)
        return ToolResult.success_result(
            answer,
            metadata={
                "url": final_url,
                "status_code": response.status_code,
                "bytes": len(response.content),
            },
        )


    @staticmethod
    def _is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
        """isPermittedRedirect: same protocol + port, no
        credentials on the redirect, and same hostname modulo a leading 'www.'."""
        try:
            o = urlparse(original_url)
            r = urlparse(redirect_url)
            if r.scheme != o.scheme:
                return False
            if (r.port or "") != (o.port or ""):
                return False
            if r.username or r.password:
                return False
            strip = lambda h: re.sub(r"^www\.", "", h or "")
            return strip(o.hostname) == strip(r.hostname)
        except ValueError:
            return False

    def _ssrf_block(self, hostname: str) -> str | None:
        if not hostname:
            return None
        if hostname in {"localhost", "metadata.google.internal"} or hostname.startswith("169.254."):
            return f"Blocked: internal address {hostname}"
        try:
            for *_, addr in socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
                ip = ipaddress.ip_address(addr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return f"Blocked: resolved to internal IP {ip}"
        except (socket.gaierror, ValueError):
            pass
        return None

    def _html_to_text(self, html: str) -> str:
        """Lightweight HTML→text."""
        html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        html = re.sub(r"(?i)<(br|/p|/div|/h[1-6]|/li)\s*/?>", "\n", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                        ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
            text = text.replace(ent, ch)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        return text.strip()

    async def _process_with_prompt(self, user_prompt: str, content: str, url: str) -> str:
        client = LLMClient(self.config)
        messages = [
            {"role": "system", "content": (
                "You answer questions about fetched web page content. Use only "
                "the provided content. Be concise and factual."
            )},
            {"role": "user", "content": (
                f"URL: {url}\n\nContent:\n{content}\n\n---\nTask: {user_prompt}"
            )},
        ]
        parts: list[str] = []
        try:
            async for event in client.chat_completion(messages=messages, tools=None, stream=True):
                if event.type == StreamEventType.TEXT_DELTA and event.text_delta:
                    parts.append(event.text_delta.content)
                elif event.type == StreamEventType.ERROR:
                    return f"[WebFetch could not summarize: {event.error}]\n\n{content[:4000]}"
        finally:
            try:
                await client.close()
            except Exception:
                pass
        answer = "".join(parts).strip()
        return answer or content[:4000]
