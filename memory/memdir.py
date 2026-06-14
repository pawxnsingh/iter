"""
Memory directory management.

MEMORY.md is an index file that gets truncated at:
  MAX_ENTRYPOINT_LINES = 200
  MAX_ENTRYPOINT_BYTES = 25,000

Memory files have frontmatter:
  ---
  name: user_role
  description: User is a senior engineer
  type: user
  ---
  Content here
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from platformdirs import user_config_dir

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000


def get_memory_dir(project_dir: str) -> Path:
    """Get the memory directory for a project."""
    slug = hashlib.md5(project_dir.encode()).hexdigest()[:16]
    return Path(user_config_dir("ai-agent")) / "projects" / slug / "memory"


def ensure_memory_dir(project_dir: str) -> Path:
    """Create memory directory if it doesn't exist."""
    path = get_memory_dir(project_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_memory_prompt(project_dir: str) -> str:
    """
    Load MEMORY.md with truncation.
    """
    memory_dir = get_memory_dir(project_dir)
    entrypoint = memory_dir / ENTRYPOINT_NAME

    if not entrypoint.exists():
        return ""

    try:
        raw = entrypoint.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

    if not raw:
        return ""

    return truncate_entrypoint(raw)


def truncate_entrypoint(raw: str) -> str:
    """
    Truncate MEMORY.md to line AND byte caps.
    """
    lines = raw.split("\n")
    line_count = len(lines)
    byte_count = len(raw)

    was_line_truncated = line_count > MAX_ENTRYPOINT_LINES
    was_byte_truncated = byte_count > MAX_ENTRYPOINT_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return raw

    truncated = "\n".join(lines[:MAX_ENTRYPOINT_LINES]) if was_line_truncated else raw

    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES)
        truncated = truncated[:cut if cut > 0 else MAX_ENTRYPOINT_BYTES]

    if was_byte_truncated and not was_line_truncated:
        reason = f"{byte_count} bytes (limit: {MAX_ENTRYPOINT_BYTES})"
    elif was_line_truncated and not was_byte_truncated:
        reason = f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})"
    else:
        reason = f"{line_count} lines and {byte_count} bytes"

    truncated += f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. Only part loaded."
    return truncated
