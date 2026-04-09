"""
Instruction loading: AGENT.md hierarchy (equivalent to Claude Code's CLAUDE.md).

Source: Claude Code src/utils/claudemd.ts (14K lines)

Loading hierarchy (lowest to highest priority):
  1. /etc/ai-agent/AGENT.md        (managed, all users)
  2. ~/.config/ai-agent/AGENT.md   (user, all projects)
  3. Walk up from CWD to root:
     - AGENT.md
     - .ai-agent/AGENT.md
     - .ai-agent/rules/*.md
  4. AGENT.local.md                (local, private)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir


@dataclass
class InstructionFile:
    """A loaded instruction file."""
    path: str
    type: str  # "managed" | "user" | "project" | "local"
    content: str


def load_all_instructions(cwd: str) -> list[InstructionFile]:
    """
    Load instruction files following Claude Code's exact hierarchy.
    Files closer to CWD have HIGHER priority (loaded last).
    """
    instructions: list[InstructionFile] = []

    # 1. Managed (/etc/ai-agent/AGENT.md)
    managed = Path("/etc/ai-agent/AGENT.md")
    if managed.exists():
        try:
            instructions.append(InstructionFile(
                str(managed), "managed", managed.read_text(encoding="utf-8")
            ))
        except Exception:
            pass

    # 2. User (~/.config/ai-agent/AGENT.md)
    user_path = Path(user_config_dir("ai-agent")) / "AGENT.md"
    if user_path.exists():
        try:
            instructions.append(InstructionFile(
                str(user_path), "user", user_path.read_text(encoding="utf-8")
            ))
        except Exception:
            pass

    # 3. Project: walk up from CWD to root
    project_files: list[InstructionFile] = []
    current = Path(cwd).resolve()

    while current != current.parent:
        # Check AGENT.md
        for name in ["AGENT.md", ".ai-agent/AGENT.md"]:
            f = current / name
            if f.is_file():
                try:
                    project_files.append(InstructionFile(
                        str(f), "project", f.read_text(encoding="utf-8")
                    ))
                except Exception:
                    pass

        # Check .ai-agent/rules/*.md
        rules_dir = current / ".ai-agent" / "rules"
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                try:
                    project_files.append(InstructionFile(
                        str(rule_file), "project", rule_file.read_text(encoding="utf-8")
                    ))
                except Exception:
                    pass

        current = current.parent

    # Reverse: files closer to CWD have higher priority (loaded last)
    instructions.extend(reversed(project_files))

    # 4. Local (AGENT.local.md — private, project-specific)
    local = Path(cwd) / "AGENT.local.md"
    if local.is_file():
        try:
            instructions.append(InstructionFile(
                str(local), "local", local.read_text(encoding="utf-8")
            ))
        except Exception:
            pass

    return instructions


def format_instructions_for_prompt(instructions: list[InstructionFile]) -> str:
    """Format loaded instructions for inclusion in system prompt."""
    if not instructions:
        return ""

    parts = []
    for instr in instructions:
        parts.append(f"# Instructions ({instr.type}: {instr.path})\n\n{instr.content}")

    return "\n\n".join(parts)
