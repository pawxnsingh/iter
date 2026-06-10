"""
MCP Client — connection to a single MCP server with reconnection.

Source: Claude Code src/services/mcp/client.ts

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
from fastmcp.client.transports import SSETransport, StdioTransport

from config.config import MCPServerConfig

logger = logging.getLogger(__name__)

# Claude Code: exponential backoff for reconnection
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


class MCPClient:
    def __init__(self, name: str, config: MCPServerConfig, cwd: Path) -> None:
        self.name = name
        self.config = config
        self.cwd = cwd
        self.status = MCPServerStatus.DISCONNECTED
        self._client: Client | None = None
        self._tools: dict[str, MCPToolInfo] = dict()
        self._reconnect_attempts = 0

    @property
    def tools(self) -> list[MCPToolInfo]:
        return list(self._tools.values())

    def _create_transport(self) -> StdioTransport | SSETransport:
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
        else:
            return SSETransport(url=self.config.url)

    async def connect(self) -> None:
        if self.status == MCPServerStatus.CONNECTED:
            return

        self.status = MCPServerStatus.CONNECTING
        try:
            self._client = Client(transport=self._create_transport())
            await self._client.__aenter__()

            tool_result = await self._client.list_tools()
            for tool in tool_result:
                self._tools[tool.name] = MCPToolInfo(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=(
                        tool.inputSchema if hasattr(tool, "inputSchema") else {}
                    ),
                    server_name=self.name,
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
        Claude Code: exponential backoff 1s initial, 30s max, 5 attempts max.
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
        if not self._client or self.status != MCPServerStatus.CONNECTED:
            # Try reconnecting
            if not await self.reconnect():
                raise RuntimeError(f"Not connected to MCP server '{self.name}'")

        try:
            result = await self._client.call_tool(tool_name, arguments)

            output = []
            for item in result.content:
                if hasattr(item, "text"):
                    output.append(item.text)
                else:
                    output.append(str(item))

            return {
                "output": "\n".join(output),
                "is_error": result.is_error,
            }
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            # Connection lost — try reconnect and retry once
            logger.warning(f"MCP call failed, attempting reconnect: {e}")
            if await self.reconnect():
                return await self.call_tool(tool_name, arguments)
            raise RuntimeError(f"MCP server '{self.name}' lost connection: {e}")
