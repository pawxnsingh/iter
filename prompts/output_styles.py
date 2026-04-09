"""
Output style configuration.

Source: Claude Code src/outputStyles/loadOutputStylesDir.ts

Users can define custom output styles in:
  - .ai-agent/output-styles/*.md
  - ~/.config/ai-agent/output-styles/*.md

Each style file has frontmatter:
  ---
  name: Concise
  description: Brief, direct responses
  ---
  Always respond in 3 sentences or fewer...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

logger = logging.getLogger(__name__)


@dataclass
class OutputStyle:
    name: str
    description: str
    prompt: str
    source: str  # "user" or "project"


def load_output_styles(cwd: str) -> list[OutputStyle]:
    """
    Load output styles from user and project directories.
    Claude Code: loadOutputStylesDir.ts
    """
    styles = []

    # User styles
    user_dir = Path(user_config_dir("ai-agent")) / "output-styles"
    if user_dir.is_dir():
        for f in sorted(user_dir.glob("*.md")):
            style = _parse_style_file(f, "user")
            if style:
                styles.append(style)

    # Project styles (override user)
    project_dir = Path(cwd) / ".ai-agent" / "output-styles"
    if project_dir.is_dir():
        for f in sorted(project_dir.glob("*.md")):
            style = _parse_style_file(f, "project")
            if style:
                styles.append(style)

    return styles


def _parse_style_file(path: Path, source: str) -> OutputStyle | None:
    """Parse a style markdown file with frontmatter."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Parse frontmatter
    if not content.startswith("---"):
        return OutputStyle(
            name=path.stem,
            description="",
            prompt=content,
            source=source,
        )

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = parts[1].strip()
    body = parts[2].strip()

    name = path.stem
    description = ""

    for line in frontmatter.split("\n"):
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip()

    return OutputStyle(
        name=name,
        description=description,
        prompt=body,
        source=source,
    )


def get_active_output_style(cwd: str, style_name: str | None = None) -> OutputStyle | None:
    """Get the active output style by name."""
    if not style_name:
        return None

    styles = load_output_styles(cwd)
    for s in styles:
        if s.name.lower() == style_name.lower():
            return s
    return None
