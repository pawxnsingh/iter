"""
Built-in agent definitions.

Each agent type is in its own module. BUILT_IN_AGENTS aggregates them so
AgentTool can look up an agent definition by type at spawn time.
"""

from tools.builtin.AgentTool.built_in.exploreAgent import EXPLORE_AGENT
from tools.builtin.AgentTool.built_in.generalPurposeAgent import GENERAL_PURPOSE_AGENT
from tools.builtin.AgentTool.built_in.planAgent import PLAN_AGENT
from tools.builtin.AgentTool.built_in.verificationAgent import VERIFICATION_AGENT
from tools.builtin.AgentTool.built_in_types import AgentDefinition

BUILT_IN_AGENTS: dict[str, AgentDefinition] = {
    GENERAL_PURPOSE_AGENT.agent_type: GENERAL_PURPOSE_AGENT,
    EXPLORE_AGENT.agent_type: EXPLORE_AGENT,
    PLAN_AGENT.agent_type: PLAN_AGENT,
    VERIFICATION_AGENT.agent_type: VERIFICATION_AGENT,
}

__all__ = [
    "BUILT_IN_AGENTS",
    "GENERAL_PURPOSE_AGENT",
    "EXPLORE_AGENT",
    "PLAN_AGENT",
    "VERIFICATION_AGENT",
    "AgentDefinition",
]
