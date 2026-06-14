"""
Session: Manages all components for one conversation.

Coordinates: LLM client, tool registry, context manager, MCP,
session persistence, and memory loading.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from client.llm_client import LLMClient
from config.config import Config
from config.loader import get_data_dir
from context.manager import ContextManager
from session.storage import SessionMetadata, SessionStorage
from tools.discovery import ToolDiscoveryManager
from tools.registry import create_default_registry


class Session:
    def __init__(self, config: Config):
        self.client = LLMClient(config=config)
        self.tool_registry = create_default_registry(config=config)
        self.context_manager = ContextManager(
            config=config,
            user_memory=self._load_memory(),
            tools=self.tool_registry.get_tools(),
        )
        self.discovery_manager = ToolDiscoveryManager(
            config=config,
            registry=self.tool_registry,
        )
        self.config = config
        self.mcp_manager = None
        self.discovery_manager.discover_all()

        self.session_id = str(uuid.uuid4())
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self._turn_count = 0

        self.storage = SessionStorage(
            session_id=self.session_id,
            project_dir=str(config.cwd),
        )

    @property
    def transcript_path(self) -> str:
        """Path to this session's JSONL transcript (includes it in every
        hook payload). Tracks storage, so it stays correct across a resume
        that re-points storage."""
        return str(self.storage.path) if getattr(self, "storage", None) else ""

    async def initialize(self) -> None:
        if self.config.mcp_servers:
            from tools.mcp.mcp_manager import MCPManager
            self.mcp_manager = MCPManager(config=self.config)
            await self.mcp_manager.initialize()
            self.mcp_manager.register_tools(self.tool_registry)

        self.discovery_manager.discover_all()

        mcp_instructions = (
            self.mcp_manager.get_server_instructions() if self.mcp_manager else None
        )
        self.context_manager = ContextManager(
            config=self.config,
            user_memory=self._load_memory(),
            tools=self.tool_registry.get_tools(),
            mcp_instructions=mcp_instructions,
        )

        self._save_metadata()

    def _load_memory(self) -> str | None:
        """Load user memory from persistent storage."""
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / "user_memory.json"

        if not path.exists():
            return None

        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            entries = data.get("entries", {})
            if not entries:
                return None

            lines = ["User preferences and notes:"]
            for key, value in entries.items():
                lines.append(f"- {key}: {value}")
            return "\n".join(lines)
        except Exception:
            return None

    def increment_turn(self) -> int:
        self._turn_count += 1
        self.updated_at = datetime.now(timezone.utc)
        return self._turn_count

    def save_message(self, role: str, content: str, **extra) -> None:
        """Persist a message to the JSONL transcript."""
        self.storage.append(role=role, content=content, **extra)

    def save_summary(self, summary: str) -> None:
        """Persist a compaction summary record."""
        self.storage.append_summary(summary)

    def _save_metadata(self) -> None:
        """Save session metadata alongside transcript."""
        self.storage.save_metadata(SessionMetadata(
            session_id=self.session_id,
            project_dir=str(self.config.cwd),
            cwd=str(self.config.cwd),
            model=self.config.model_name,
            created_at=self.created_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
            turn_count=self._turn_count,
            message_count=self.context_manager.message_count,
        ))

    def get_stats(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "turn_count": self._turn_count,
            "tools_count": len(self.tool_registry.get_tools()),
            "message_count": self.context_manager.message_count,
        }
