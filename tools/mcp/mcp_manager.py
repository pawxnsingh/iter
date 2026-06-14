import asyncio
import re
from typing import Any

from config.config import Config
from tools.mcp.client import MCPClient, MCPServerStatus
from tools.mcp.mcp_tool import MCPTool
from tools.registry import ToolRegistry


def _normalize_name_for_mcp(name: str) -> str:
    """Replace chars outside [A-Za-z0-9_-] with _ so
    the qualified tool name satisfies the API name pattern ^[A-Za-z0-9_-]{1,64}$."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _build_mcp_tool_name(server: str, tool: str) -> str:
    """buildMcpToolName: mcp__<server>__<tool> with both parts normalized."""
    return f"mcp__{_normalize_name_for_mcp(server)}__{_normalize_name_for_mcp(tool)}"


class MCPManager:
    def __init__(self, config: Config):
        self.config = config
        self._clients: dict[str, MCPClient] = {}
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return

        mcp_configs = self.config.mcp_servers

        if not mcp_configs:
            return

        for name, server_config in mcp_configs.items():
            if not server_config.enabled:
                continue

            self._clients[name] = MCPClient(
                name=name,
                config=server_config,
                cwd=self.config.cwd,
            )

        connection_tasks = [
            asyncio.wait_for(
                client.connect(),
                timeout=client.config.startup_timeout_sec,
            )
            for name, client in self._clients.items()
        ]

        await asyncio.gather(*connection_tasks, return_exceptions=True)

        self._initialized = True

    def register_tools(self, registry: ToolRegistry) -> int:
        count = 0

        for client in self._clients.values():
            if client.status != MCPServerStatus.CONNECTED:
                continue

            for tool_info in client.tools:
                mcp_tool = MCPTool(
                    tool_info=tool_info,
                    client=client,
                    config=self.config,
                    name=_build_mcp_tool_name(client.name, tool_info.name),
                )
                registry.register_mcp_tool(mcp_tool)
                count += 1

        return count

    async def shutdown(self) -> None:
        disconnection_tasks = [client.disconnect() for client in self._clients.values()]

        await asyncio.gather(*disconnection_tasks, return_exceptions=True)

        self._clients.clear()
        self._initialized = False

    def get_all_servers(self) -> list[dict[str, Any]]:
        servers = []
        for name, client in self._clients.items():
            server_info = {
                "name": name,
                "status": client.status.value,
                "tools": len(client.tools),
            }
            servers.append(server_info)

        return servers

    def get_server_instructions(self) -> list[tuple[str, str]]:
        """(name, instructions) for each CONNECTED server that provided usage
        instructions — fed into the system prompt's MCP-instructions section
."""
        from tools.mcp.client import MCPServerStatus
        out: list[tuple[str, str]] = []
        for name, client in self._clients.items():
            if client.status == MCPServerStatus.CONNECTED and client.instructions:
                out.append((name, client.instructions))
        return out
