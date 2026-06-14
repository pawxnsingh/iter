"""
MCP Client — connection to a single MCP server with reconnection.

Features:
  - Stdio and SSE transport
  - Tool discovery
  - Automatic reconnection with exponential backoff (max 5 attempts)
  - Health checking
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)

from config.config import MCPServerConfig

logger = logging.getLogger(__name__)

MAX_RECONNECT_ATTEMPTS = 5
INITIAL_BACKOFF_SEC = 1
MAX_BACKOFF_SEC = 30


class MCPServerStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class MCPToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""
    read_only: bool = False
    destructive: bool = False


def _map_mcp_content(content: Any, server_name: str = "") -> tuple[str, list[dict[str, str]]]:
    """Map an MCP result content array to (text, image-attachments).

    Mirrors transformResultContent:
      - TextContent → text.
      - ImageContent → ONLY an image block, NO accompanying text (sends no
        synthetic "[image]" text).
      - EmbeddedResource → prefixed with "[Resource from {server} at {uri}] ":
        text resource → prefix + text; image blob → prefix text block + image
        block; other blob → prefix + a binary placeholder.
      - resource_link → "[Resource link: {name}] {uri}".
    """
    text_parts: list[str] = []
    images: list[dict[str, str]] = []
    for item in content or []:
        itype = getattr(item, "type", None)
        if getattr(item, "text", None) is not None and itype != "audio":
            text_parts.append(item.text)
        elif itype == "audio":
            mime = getattr(item, "mimeType", None) or "audio"
            text_parts.append(f"[Audio from {server_name}] ({mime})")
        elif itype == "image" or (hasattr(item, "data") and itype != "resource"):
            data = getattr(item, "data", None)
            mime = getattr(item, "mimeType", None) or "image/png"
            if data:
                images.append({"media_type": mime, "data": data})
        elif itype == "resource" or hasattr(item, "resource"):
            res = getattr(item, "resource", None)
            uri = getattr(res, "uri", "") if res is not None else ""
            prefix = f"[Resource from {server_name} at {uri}] "
            if res is not None and getattr(res, "text", None):
                text_parts.append(f"{prefix}{res.text}")
            elif res is not None and getattr(res, "blob", None):
                rmime = getattr(res, "mimeType", "") or ""
                if rmime.startswith("image/"):
                    images.append({"media_type": rmime, "data": res.blob})
                    text_parts.append(prefix.rstrip())
                else:
                    text_parts.append(f"{prefix}[{rmime or 'binary'} resource]")
        elif itype == "resource_link":
            name = getattr(item, "name", "") or ""
            uri = getattr(item, "uri", "") or ""
            desc = getattr(item, "description", "") or ""
            line = (
                f"[Resource link: {name}] {uri}".rstrip() if name
                else f"[Resource link: {uri}]"
            )
            if desc:
                line += f" ({desc})"
            text_parts.append(line)
        else:
            text_parts.append(str(item))
    return "\n".join(text_parts), images


class MCPClient:
    def __init__(self, name: str, config: MCPServerConfig, cwd: Path) -> None:
        self.name = name
        self.config = config
        self.cwd = cwd
        self.status = MCPServerStatus.DISCONNECTED
        self._client: Client | None = None
        self._tools: dict[str, MCPToolInfo] = dict()
        self._reconnect_attempts = 0
        self.instructions: str | None = None

    @property
    def tools(self) -> list[MCPToolInfo]:
        return list(self._tools.values())

    def _create_transport(self) -> StdioTransport | SSETransport | StreamableHttpTransport:
        if self.config.command:
            env = os.environ.copy()
            env.update(self.config.env)
            return StdioTransport(
                command=self.config.command,
                args=list(self.config.args),
                env=env,
                cwd=str(self.config.cwd or self.cwd),
                log_file=Path(os.devnull),
            )
        if (self.config.type or "sse").lower() == "http":
            return StreamableHttpTransport(url=self.config.url)
        return SSETransport(url=self.config.url)

    async def connect(self) -> None:
        if self.status == MCPServerStatus.CONNECTED:
            return

        self.status = MCPServerStatus.CONNECTING
        try:
            conn_timeout_s = int(os.environ.get("MCP_TIMEOUT", "30000")) / 1000
            self._client = Client(transport=self._create_transport())
            await asyncio.wait_for(self._client.__aenter__(), timeout=conn_timeout_s)

            init_result = getattr(self._client, "initialize_result", None)
            raw_instr = getattr(init_result, "instructions", None) if init_result else None
            if raw_instr:
                self.instructions = (
                    raw_instr[:2048] + "… [truncated]" if len(raw_instr) > 2048 else raw_instr
                )

            tool_result = await asyncio.wait_for(
                self._client.list_tools(), timeout=conn_timeout_s
            )
            for tool in tool_result:
                ann = getattr(tool, "annotations", None)
                self._tools[tool.name] = MCPToolInfo(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=(
                        tool.inputSchema if hasattr(tool, "inputSchema") else {}
                    ),
                    server_name=self.name,
                    read_only=bool(getattr(ann, "readOnlyHint", False)) if ann else False,
                    destructive=bool(getattr(ann, "destructiveHint", False)) if ann else False,
                )

            self.status = MCPServerStatus.CONNECTED
            self._reconnect_attempts = 0
            logger.info(f"MCP server '{self.name}' connected ({len(self._tools)} tools)")
        except Exception as e:
            self.status = MCPServerStatus.ERROR
            logger.warning(f"MCP server '{self.name}' connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
        self._tools.clear()
        self.status = MCPServerStatus.DISCONNECTED

    async def reconnect(self) -> bool:
        """
        Reconnect with exponential backoff.
        exponential backoff 1s initial, 30s max, 5 attempts max.
        """
        if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            logger.error(f"MCP server '{self.name}' max reconnect attempts reached")
            return False

        self._reconnect_attempts += 1
        backoff = min(INITIAL_BACKOFF_SEC * (2 ** (self._reconnect_attempts - 1)), MAX_BACKOFF_SEC)
        self.status = MCPServerStatus.RECONNECTING

        logger.info(f"MCP server '{self.name}' reconnecting in {backoff}s (attempt {self._reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})")
        await asyncio.sleep(backoff)

        try:
            await self.disconnect()
            await self.connect()
            return True
        except Exception as e:
            logger.warning(f"MCP reconnection failed: {e}")
            return False

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]):
        """Call a tool with automatic reconnection on failure."""
        is_stdio = self.config.command is not None
        if not self._client or self.status != MCPServerStatus.CONNECTED:
            if is_stdio or not await self.reconnect():
                raise RuntimeError(f"Not connected to MCP server '{self.name}'")

        try:
            timeout_s = int(os.environ.get("MCP_TOOL_TIMEOUT", "100000000")) / 1000
            result = await asyncio.wait_for(
                self._client.call_tool(tool_name, arguments, raise_on_error=False),
                timeout=timeout_s,
            )

            import json as _json
            images: list[dict[str, str]] = []
            tool_result = getattr(result, "toolResult", None)
            if tool_result is None:
                tool_result = getattr(result, "tool_result", None)
            structured = getattr(result, "structured_content", None)
            if tool_result is not None:
                output = str(tool_result)
            elif structured is not None:
                try:
                    output = _json.dumps(structured, default=str)
                except (TypeError, ValueError):
                    output = str(structured)
            else:
                output, images = _map_mcp_content(result.content, self.name)

            if result.is_error:
                for item in (result.content or []):
                    if getattr(item, "text", None):
                        output = item.text
                        break

            return {
                "output": output,
                "is_error": result.is_error,
                "images": images,
            }
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            logger.warning(f"MCP call failed: {e}")
            if not is_stdio and await self.reconnect():
                return await self.call_tool(tool_name, arguments)
            raise RuntimeError(f"MCP server '{self.name}' lost connection: {e}")
