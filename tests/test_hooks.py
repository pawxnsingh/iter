"""Tests for hook system."""

from core_types.hooks import HookCommand, HookEvent
from hooks.config import (
    capture_hooks_snapshot,
    get_hooks_from_snapshot,
    reset_hooks_snapshot,
)
from hooks.executor import HookExecutor


def test_all_hook_events():
    # 27 from Claude Code + PostSampling = 28
    assert len(HookEvent) == 28


def test_hook_events_include_critical():
    events = {e.value for e in HookEvent}
    assert "PreToolUse" in events
    assert "PostToolUse" in events
    assert "PostSampling" in events
    assert "SessionStart" in events
    assert "SessionEnd" in events
    assert "UserPromptSubmit" in events
    assert "Stop" in events
    assert "PreCompact" in events
    assert "PostCompact" in events
    assert "PermissionRequest" in events
    assert "FileChanged" in events
    assert "CwdChanged" in events


def test_hook_executor_no_hooks():
    executor = HookExecutor()
    assert not executor.has_hooks("PreToolUse")


def test_hook_snapshot_pattern():
    reset_hooks_snapshot()
    config = {
        "PreToolUse": [HookCommand(command="echo test")],
    }
    capture_hooks_snapshot(config)
    snapshot = get_hooks_from_snapshot()
    assert "PreToolUse" in snapshot
    assert len(snapshot["PreToolUse"]) == 1
    reset_hooks_snapshot()


def test_hook_snapshot_empty():
    reset_hooks_snapshot()
    snapshot = get_hooks_from_snapshot()
    assert snapshot == {}
