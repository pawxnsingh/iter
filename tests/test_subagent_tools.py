"""Subagent tool-restriction parity: Explore/Plan get write tools removed."""
from config.config import Config
from tools.builtin.AgentTool.built_in import BUILT_IN_AGENTS
from tools.registry import create_default_registry


def _allowed_for(agent_type: str) -> list[str]:
    defn = BUILT_IN_AGENTS[agent_type]
    cfg = Config()
    all_names = [t.name for t in create_default_registry(cfg).get_tools()]
    if defn.tools and defn.tools != ["*"]:
        return defn.tools
    if defn.disallowed_tools:
        return [n for n in all_names if n not in set(defn.disallowed_tools)]
    return all_names


def _subagent_registry_names(agent_type: str) -> list[str]:
    cfg = Config()
    cfg.allowed_tools = _allowed_for(agent_type)
    return [t.name for t in create_default_registry(cfg).get_tools()]


def test_explore_excludes_write_tools():
    names = _subagent_registry_names("Explore")
    for blocked in ("Edit", "Write", "NotebookEdit", "Agent", "ExitPlanMode"):
        assert blocked not in names, f"{blocked} should be excluded for Explore"
    assert "Read" in names and "Grep" in names and "Glob" in names


def test_plan_excludes_write_tools():
    names = _subagent_registry_names("Plan")
    for blocked in ("Edit", "Write", "NotebookEdit"):
        assert blocked not in names, f"{blocked} should be excluded for Plan"
    assert "Read" in names


def test_general_purpose_has_all_tools():
    # general-purpose declares tools=["*"] → no restriction
    names = _subagent_registry_names("general-purpose")
    assert "Edit" in names and "Write" in names and "Read" in names
