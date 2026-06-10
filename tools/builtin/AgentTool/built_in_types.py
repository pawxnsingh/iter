"""
AgentDefinition dataclass. Mirrors Claude Code's BuiltInAgentDefinition
from src/tools/AgentTool/loadAgentsDir.ts.

Lives at the AgentTool/ root (not inside built_in/) so the dataclass
can be imported by both AgentTool.py and the per-agent built_in/*.py
files without a circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AgentDefinition:
    agent_type: str
    when_to_use: str
    # When None: agent has access to all tools (subject to disallowed_tools)
    tools: list[str] | None = None
    # Tools explicitly NOT available to this agent (typically write tools for
    # read-only agents like Explore/Plan).
    disallowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 200
    # 'inherit' = use the parent agent's model; 'haiku' / 'sonnet' / 'opus' /
    # exact id otherwise.
    model: str = "inherit"
    # Either a static system prompt string OR a zero-arg function returning one.
    # Use a function when the prompt depends on runtime config/feature flags.
    system_prompt: str | Callable[[], str] | None = None
    # UI/permission knobs that CC's BuiltInAgentDefinition carries. Most are
    # only honored when their respective UI/feature is built (e.g. `color`,
    # `background`, `permission_mode`). Keeping them on the dataclass keeps
    # built_in/*.py files structurally identical to CC's *.ts.
    color: str | None = None
    background: bool = False
    omit_claude_md: bool = False
    permission_mode: str | None = None
    # Short description shown in tool listings — derived from when_to_use
    # if not supplied (matches the way CC's tool prompts render).
    description: str = ""

    def __post_init__(self) -> None:
        if not self.description:
            # Use first sentence of when_to_use as short description fallback
            first_sentence = self.when_to_use.split(".")[0]
            self.description = first_sentence[:200]

    def resolve_system_prompt(self) -> str | None:
        """Return the system prompt string, calling it if it's a function."""
        if callable(self.system_prompt):
            return self.system_prompt()
        return self.system_prompt
