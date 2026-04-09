"""Tests for permission system."""
import asyncio


from config.config import Config
from core_types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from permissions.checker import PermissionChecker
from tools.builtin.edit_file import EditFileTool
from tools.builtin.grep import GrepTool
from tools.builtin.shell import ShellTool

config = Config()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_bypass_mode_allows_all():
    ctx = ToolPermissionContext(mode=PermissionMode.BYPASS)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(EditFileTool(config), {}))
    assert result.behavior == PermissionBehavior.ALLOW


def test_plan_mode_blocks_all():
    ctx = ToolPermissionContext(mode=PermissionMode.PLAN)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(GrepTool(config), {}))
    assert result.behavior == PermissionBehavior.DENY


def test_default_mode_allows_reads():
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(GrepTool(config), {}))
    assert result.behavior == PermissionBehavior.ALLOW


def test_default_mode_asks_for_writes():
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(EditFileTool(config), {}))
    assert result.behavior == PermissionBehavior.ASK


def test_dont_ask_mode_denies_unmatched():
    ctx = ToolPermissionContext(mode=PermissionMode.DONT_ASK)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(EditFileTool(config), {}))
    assert result.behavior == PermissionBehavior.DENY


def test_destructive_command_asks():
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)
    checker = PermissionChecker(ctx)
    shell = ShellTool(config)
    result = _run(checker.check(shell, {"command": "rm -rf /tmp"}))
    assert result.behavior == PermissionBehavior.ASK
    assert "DESTRUCTIVE" in result.message
