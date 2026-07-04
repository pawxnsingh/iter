"""
Session storage: JSONL-based transcript persistence.

Each message is one JSON line, append-only (crash-safe).
Sessions can be listed, loaded, and resumed.

Directory structure:
  ~/.config/ai-agent/projects/<slug>/
    transcripts/<session_id>.jsonl
    transcripts/<session_id>.meta.json
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from utils.logger import get_logger

logger = get_logger(__name__)

_APP_VERSION = "0.1.0"
_git_branch_cache: dict[str, str] = {}


def _git_branch(cwd: str) -> str:
    """Current git branch for the cwd. Cached."""
    if cwd in _git_branch_cache:
        return _git_branch_cache[cwd]
    branch = ""
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0:
            branch = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        branch = ""
    _git_branch_cache[cwd] = branch
    return branch


@dataclass
class SessionMetadata:
    """Metadata for a session, stored alongside the transcript."""

    session_id: str
    project_dir: str
    cwd: str
    model: str
    created_at: str
    updated_at: str
    turn_count: int = 0
    total_tokens: int = 0
    message_count: int = 0


def _sanitize_path(path: str) -> str:
    """Create a filesystem-safe slug from a project path."""
    return hashlib.md5(path.encode()).hexdigest()[:16]


def _get_project_dir(project_path: str) -> Path:
    """Get the project storage directory."""
    slug = _sanitize_path(project_path)
    return Path(user_config_dir("ai-agent")) / "projects" / slug


class SessionStorage:
    """
    JSONL-based session persistence.

    Each message is one JSON line. This is crash-safe:
    if the process dies mid-write, you lose at most one line.
    """

    def __init__(self, session_id: str, project_dir: str):
        import re

        if not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", session_id):
            raise ValueError(f"Invalid session ID: {session_id}")

        base = _get_project_dir(project_dir) / "transcripts"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{session_id}.jsonl"
        self.meta_path = base / f"{session_id}.meta.json"
        self.session_id = session_id
        self.project_dir = project_dir
        self._last_uuid: str | None = None

    def append(self, role: str, content: str, **extra) -> None:
        """
        Append a message to the transcript with chain integrity (parentUuid).
        messages linked via parentUuid → validates on resume.

        / cwd / version / userType / isSidechain, plus our flat role/content/
        tool fields for round-trip fidelity (nests these under `message`; we
        keep them flat so our loader reads them directly).
        """
        import uuid as uuid_mod

        msg_uuid = str(uuid_mod.uuid4())
        is_sidechain = extra.pop("is_sidechain", False)
        entry = {
            "parentUuid": self._last_uuid,
            "isSidechain": is_sidechain,
            "userType": "external",
            "type": "assistant" if role == "assistant" else "user",
            "uuid": msg_uuid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sessionId": self.session_id,
            "cwd": self.project_dir,
            "version": _APP_VERSION,
            "gitBranch": _git_branch(self.project_dir),
            "role": role,
            "content": content,
            **extra,
        }
        self._last_uuid = msg_uuid
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def append_summary(self, summary: str) -> None:
        """Persist a compaction summary as a `type:'summary'` record
        keyed to the current chain leaf via leafUuid,
               so a resume can see that compaction happened here."""
        entry = {
            "type": "summary",
            "summary": summary,
            "leafUuid": self._last_uuid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def save_metadata(self, meta: SessionMetadata) -> None:
        """Save session metadata alongside the transcript."""
        meta.updated_at = datetime.now(timezone.utc).isoformat()
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(asdict(meta), f, indent=2)

    @classmethod
    def load_transcript(cls, path: str | Path) -> list[dict[str, Any]]:
        """Load all messages from a JSONL transcript file. Skips corrupt lines."""
        messages = []
        with open(path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"Skipping corrupt line {line_num} in {path}")
        return messages

    @classmethod
    def list_sessions(cls, project_dir: str) -> list[SessionMetadata]:
        """List all sessions for a project, sorted by date (newest first)."""
        base = _get_project_dir(project_dir) / "transcripts"
        if not base.exists():
            return []

        sessions = []
        for meta_file in sorted(
            base.glob("*.meta.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                sessions.append(SessionMetadata(**data))
            except Exception:
                continue
        return sessions

    @classmethod
    def get_latest_session(cls, project_dir: str) -> SessionMetadata | None:
        """Get the most recent session for a project."""
        sessions = cls.list_sessions(project_dir)
        return sessions[0] if sessions else None
