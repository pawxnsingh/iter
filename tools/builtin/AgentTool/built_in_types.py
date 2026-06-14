"""
AgentDefinition dataclass. Mirrors the BuiltInAgentDefinition

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
    tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 200
    model: str = "inherit"
    system_prompt: str | Callable[[], str] | None = None
    color: str | None = None
    background: bool = False
    permission_mode: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.description:
            first_sentence = self.when_to_use.split(".")[0]
            self.description = first_sentence[:200]

    def resolve_system_prompt(self) -> str | None:
        """Return the system prompt string, calling it if it's a function."""
        if callable(self.system_prompt):
            return self.system_prompt()
        return self.system_prompt
