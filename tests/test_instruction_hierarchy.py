"""Full instruction hierarchy reaches the system prompt.

Regression: load_all_instructions collected managed/user/parent/rules/local but
only fired the InstructionsLoaded hook; only the single cwd AGENT.md reached the
model. Now the whole hierarchy is folded into the system prompt (deduped).
"""
import asyncio
import tempfile
from pathlib import Path

from config.loader import load_config
from agent.agent import Agent


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_all_instruction_tiers_in_prompt():
    async def go():
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); proj = root / "project"; sub = proj / "sub"; sub.mkdir(parents=True)
            (proj / "AGENT.md").write_text("PARENT_RULE: prefer composition")
            (sub / "AGENT.md").write_text("CWD_RULE: use type hints")
            rules = sub / ".ai-agent" / "rules"; rules.mkdir(parents=True)
            (rules / "style.md").write_text("RULE_FILE: 2-space indent")
            (sub / "AGENT.local.md").write_text("LOCAL_RULE: no print debugging")
            cfg = load_config(cwd=sub)
            async with Agent(cfg) as ag:
                reminder = ag._user_context_reminder
                assert "PARENT_RULE" in reminder
                assert "CWD_RULE" in reminder
                assert "RULE_FILE" in reminder
                assert "LOCAL_RULE" in reminder
                assert reminder.count("use type hints") == 1
                assert "CWD_RULE" not in ag.session.context_manager.system_prompt

    _run(go())
