"""Mirrors Claude Code src/tools/AgentTool/constants.ts."""

AGENT_TOOL_NAME = "Agent"
# Legacy wire name for backward compat (permission rules, hooks, resumed sessions)
LEGACY_AGENT_TOOL_NAME = "Task"
VERIFICATION_AGENT_TYPE = "verification"

# Built-in agents that run once and return a report — the parent never
# SendMessages back to continue them. Mirrors CC's ONE_SHOT_BUILTIN_AGENT_TYPES.
ONE_SHOT_BUILTIN_AGENT_TYPES = frozenset(("Explore", "Plan"))
