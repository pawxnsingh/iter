"""
Round-5 parity regressions for the settings loader + hook executor — the
subsystems whose prior "verified" marks were revoked. Pins behavior:
settings precedence, array-concat merge, nested hook shape, matcher semantics,
hook output channels, and the exit-code-2 blocking protocol.
"""
import asyncio

from config.settings import (
    apply_settings_env,
    deep_merge,
    extract_hooks_from_settings,
    get_default_permission_mode,
)
from core_types.hooks import HookCommand
from hooks.executor import _matches_pattern, _parse_hook_json, HookExecutor


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_deep_merge_concatenates_and_dedupes_arrays():
    base = {"permissions": {"allow": ["Read", "Bash(git status)"]}}
    override = {"permissions": {"allow": ["Read", "Write"]}}
    merged = deep_merge(base, override)
    assert merged["permissions"]["allow"] == ["Read", "Bash(git status)", "Write"]


def test_deep_merge_scalars_override():
    assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}


def test_apply_settings_env_overrides_existing(monkeypatch):
    monkeypatch.delenv("PARITY_TEST_VAR", raising=False)
    monkeypatch.setenv("PARITY_EXISTING", "real")
    apply_settings_env({"env": {"PARITY_TEST_VAR": "from_settings", "PARITY_EXISTING": "overridden"}})
    import os
    assert os.environ["PARITY_TEST_VAR"] == "from_settings"
    assert os.environ["PARITY_EXISTING"] == "overridden"


def test_get_default_permission_mode():
    assert get_default_permission_mode({"permissions": {"defaultMode": "plan"}}) == "plan"
    assert get_default_permission_mode({"permissions": {}}) is None
    assert get_default_permission_mode({}) is None


def test_extract_hooks_nested_cc_shape():
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit|Write",
                 "hooks": [{"type": "command", "command": "echo hi"}]},
            ]
        }
    }
    out = extract_hooks_from_settings(settings)
    assert "PreToolUse" in out
    cmd = out["PreToolUse"][0]
    assert cmd.command == "echo hi"
    assert cmd.match == "Edit|Write"


def test_extract_hooks_ignores_garbage():
    assert extract_hooks_from_settings({"hooks": {"X": "notalist"}}) == {}


def test_matches_pattern():
    assert _matches_pattern("Bash", None)
    assert _matches_pattern("Bash", "*")
    assert _matches_pattern("Edit", "Edit|Write")
    assert not _matches_pattern("Bash", "Edit|Write")
    assert _matches_pattern("Bash", "^Ba.h$")
    assert not _matches_pattern("Read", "^Ba.h$")
    assert _matches_pattern("Bash", "Bash")


def test_parse_permission_decision_deny_blocks():
    r = _parse_hook_json({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "nope"}})
    assert r.blocking_error == "nope"


def test_parse_decision_block_blocks():
    r = _parse_hook_json({"decision": "block", "reason": "stop"})
    assert r.blocking_error == "stop"


def test_parse_continue_false_prevents_continuation():
    r = _parse_hook_json({"continue": False})
    assert r.prevent_continuation is True


def test_parse_plain_success():
    r = _parse_hook_json({"additionalContext": "fyi"})
    assert r.blocking_error is None
    assert r.additional_context == "fyi"


def test_exit_code_2_blocks_with_stderr():
    ex = HookExecutor({"PreToolUse": [HookCommand(
        command="echo 'blocked by policy' >&2; exit 2")]})
    r = _run(ex.run_pre_tool_hooks(tool_name="Bash", tool_input={}, cwd="."))
    assert r.blocking_error is not None
    assert "blocked by policy" in r.blocking_error


def test_exit_code_0_json_deny_blocks():
    ex = HookExecutor({"PreToolUse": [HookCommand(
        command="""echo '{"decision":"block","reason":"denied"}'""")]})
    r = _run(ex.run_pre_tool_hooks(tool_name="Bash", tool_input={}, cwd="."))
    assert r.blocking_error == "denied"


def test_matcher_filters_non_matching_tool():
    ex = HookExecutor({"PreToolUse": [HookCommand(
        command="exit 2", match="Edit")]})
    r = _run(ex.run_pre_tool_hooks(tool_name="Bash", tool_input={}, cwd="."))
    assert r.blocking_error is None


def test_hook_nonzero_exit_with_json_honors_json():
    """parses stdout JSON FIRST regardless of exit code — a hook that exits 1
    but emits JSON must have its JSON honored, not dropped as an error."""
    ex = HookExecutor({"PreToolUse": [HookCommand(
        command="""echo '{"decision":"block","reason":"viajson"}'; exit 1""")]})
    r = _run(ex.run_pre_tool_hooks(tool_name="Bash", tool_input={}, cwd="."))
    assert r.blocking_error == "viajson"
