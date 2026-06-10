"""Tests for permission system."""
import asyncio


from config.config import Config
from core_types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from permissions.checker import PermissionChecker
from tools.builtin.FileEditTool import FileEditTool
from tools.builtin.GrepTool import GrepTool
from tools.builtin.BashTool import BashTool

config = Config()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_bypass_mode_allows_all():
    ctx = ToolPermissionContext(mode=PermissionMode.BYPASS)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(FileEditTool(config), {}))
    assert result.behavior == PermissionBehavior.ALLOW


def test_plan_mode_allows_reads_blocks_writes():
    """Plan mode (CC parity): read-only tools allowed so the agent can
    investigate while building a plan; write/mutate tools are blocked."""
    ctx = ToolPermissionContext(mode=PermissionMode.PLAN)
    checker = PermissionChecker(ctx)
    # Read-only tool: Grep → allowed
    result = _run(checker.check(GrepTool(config), {}))
    assert result.behavior == PermissionBehavior.ALLOW
    # Write tool: Edit → denied
    result = _run(checker.check(FileEditTool(config), {}))
    assert result.behavior == PermissionBehavior.DENY


def test_default_mode_allows_reads():
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(GrepTool(config), {}))
    assert result.behavior == PermissionBehavior.ALLOW


def test_default_mode_asks_for_writes():
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(FileEditTool(config), {}))
    assert result.behavior == PermissionBehavior.ASK


def test_dont_ask_mode_denies_unmatched():
    ctx = ToolPermissionContext(mode=PermissionMode.DONT_ASK)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(FileEditTool(config), {}))
    assert result.behavior == PermissionBehavior.DENY


def test_destructive_command_asks():
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)
    checker = PermissionChecker(ctx)
    shell = BashTool(config)
    result = _run(checker.check(shell, {"command": "rm -rf /tmp"}))
    assert result.behavior == PermissionBehavior.ASK
    assert "DESTRUCTIVE" in result.message


def test_dont_ask_allows_read_only_cc_parity():
    """CC parity (permissions.ts:503): dontAsk converts ask→deny at the end,
    but decisions that resolve to allow (read-only auto-allow) are unaffected.
    A read-only tool — and a read-only-classified Bash command — stay allowed."""
    ctx = ToolPermissionContext(mode=PermissionMode.DONT_ASK)
    checker = PermissionChecker(ctx)
    assert _run(checker.check(GrepTool(config), {"pattern": "x"})).behavior == PermissionBehavior.ALLOW
    assert _run(checker.check(BashTool(config), {"command": "ls"})).behavior == PermissionBehavior.ALLOW


def test_dont_ask_denies_non_readonly_via_ask_transform():
    """A non-read-only command with no allow rule would ask → converted to deny."""
    ctx = ToolPermissionContext(mode=PermissionMode.DONT_ASK)
    checker = PermissionChecker(ctx)
    assert _run(checker.check(BashTool(config), {"command": "touch newfile"})).behavior == PermissionBehavior.DENY


def test_dont_ask_respects_allowlist():
    """An explicit allow rule resolves to allow before the ask→deny transform."""
    ctx = ToolPermissionContext(
        mode=PermissionMode.DONT_ASK, allow_rules={"Bash": ["touch *"]}
    )
    checker = PermissionChecker(ctx)
    assert _run(checker.check(BashTool(config), {"command": "touch newfile"})).behavior == PermissionBehavior.ALLOW
