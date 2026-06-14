"""Round-10 -parity regressions (1:1 directive).

Locks the behaviors closed in round 10 so they don't silently regress. Each test
pins a -verified behavior; comments cite the source.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from config.loader import load_config
from tools.base import ToolInvocation


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_env_block_model_description_and_cutoff():
    from prompts.system import _environment
    cfg = load_config(cwd=Path.cwd())
    cfg.model.name = "claude-opus-4-6"
    env = _environment(cfg)
    assert "You are powered by the model named Opus 4.6." in env
    assert "Assistant knowledge cutoff is May 2025." in env
    assert "  - Is a git repository:" in env
    assert "You have been invoked in the following environment: \n" in env
    assert "Claude Code" not in env  # no product marketing lines


def test_env_block_unknown_model_graceful():
    from prompts.system import _environment
    cfg = load_config(cwd=Path.cwd())
    cfg.model.name = "gpt-5.2"
    env = _environment(cfg)
    assert "You are powered by the model gpt-5.2." in env
    assert "knowledge cutoff" not in env


def test_agentmd_has_instruction_prefix_and_type_descriptions():
    from context.instructions import build_user_context_reminder
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "AGENT.md").write_text("rule one")
        r = build_user_context_reminder(str(d), "2026-06-10")
        assert "IMPORTANT: These instructions OVERRIDE any default behavior" in r
        assert "(project instructions, checked into the codebase)" in r


def test_accept_edits_respects_ask_rule():
    from permissions.checker import PermissionChecker
    from core_types.permissions import PermissionMode, ToolPermissionContext, PermissionBehavior
    from tools.builtin.FileWriteTool.FileWriteTool import FileWriteTool
    cfg = load_config(cwd=Path("/tmp"))
    ctx = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, working_directories=("/tmp",),
                                allow_rules={}, deny_rules={}, ask_rules={"Write": ["/tmp/*"]})
    r = _run(PermissionChecker(ctx).check(FileWriteTool(cfg), {"file_path": "/tmp/x", "content": "y"}))
    assert r.behavior == PermissionBehavior.ASK


def test_hook_allow_respects_deny_rule():
    from permissions.checker import PermissionChecker
    from core_types.permissions import PermissionMode, ToolPermissionContext, PermissionBehavior
    from tools.builtin.BashTool.BashTool import BashTool
    cfg = load_config(cwd=Path("/tmp"))
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT, working_directories=("/tmp",),
                                allow_rules={}, deny_rules={"Bash": ["rm *"]}, ask_rules={})
    r = PermissionChecker(ctx).check_rules_only(BashTool(cfg), {"command": "echo ok && rm -rf x"})
    assert r is not None and r.behavior == PermissionBehavior.DENY


def test_wildcard_single_star_gate():
    from permissions.rules import _match_wildcard
    assert _match_wildcard("git *", "git") is True
    assert _match_wildcard("* run *", "npm run") is False


def test_protected_path_basename_and_dir():
    from permissions.filesystem import is_protected_auto_edit_path
    assert is_protected_auto_edit_path("/home/x/.bashrc") is True
    assert is_protected_auto_edit_path("/proj/.git/config") is True
    assert is_protected_auto_edit_path("/proj/src/main.py") is False


def test_event_hook_matcher_and_accumulation():
    from hooks.executor import HookExecutor
    from core_types.hooks import HookCommand, HookEvent
    hooks = {HookEvent.SESSION_START: [
        HookCommand(type="command", match="resume", command='echo R'),
        HookCommand(type="command", match="startup", command='echo S'),
        HookCommand(type="command", match=None, command='echo A'),
    ]}
    r = _run(HookExecutor(hooks).run_event_hooks(event=HookEvent.SESSION_START, data={"source": "startup"}))
    assert "S" in r.additional_context and "A" in r.additional_context
    assert "R" not in r.additional_context


def test_env_scrub_exact_list_and_truthiness():
    from hooks.executor import _filter_env_secrets, _is_env_truthy
    assert _is_env_truthy("0") is False and _is_env_truthy("1") is True
    env = {"GITHUB_TOKEN": "x", "ANTHROPIC_API_KEY": "y"}
    prev = os.environ.get("SUBPROCESS_ENV_SCRUB")
    os.environ["SUBPROCESS_ENV_SCRUB"] = "1"
    try:
        out = _filter_env_secrets(env)
    finally:
        if prev is None:
            os.environ.pop("SUBPROCESS_ENV_SCRUB", None)
        else:
            os.environ["SUBPROCESS_ENV_SCRUB"] = prev
    assert "ANTHROPIC_API_KEY" not in out
    assert "GITHUB_TOKEN" in out


def test_grep_files_mode_header_and_nomatch():
    from tools.builtin.GrepTool.GrepTool import GrepTool
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); (d / "a.py").write_text("needle\n")
        g = GrepTool(load_config(cwd=d))
        hit = _run(g.execute(ToolInvocation(params={"pattern": "needle", "path": str(d),
                   "output_mode": "files_with_matches"}, cwd=d)))
        assert hit.output.startswith("Found 1 file\n")
        miss = _run(g.execute(ToolInvocation(params={"pattern": "zzzq", "path": str(d),
                    "output_mode": "files_with_matches"}, cwd=d)))
        assert miss.output == "No files found"


def test_fileread_over_token_limit_errors():
    from tools.builtin.FileReadTool.FileReadTool import FileReadTool
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); big = d / "big.txt"; big.write_text("word " * 200_000)
        r = _run(FileReadTool(load_config(cwd=d)).execute(
            ToolInvocation(params={"file_path": str(big)}, cwd=d)))
        assert not r.success
        assert (r.error or "").startswith("File content")


def test_edit_preserves_curly_quote_style():
    from tools.builtin.FileEditTool.FileEditTool import _preserve_quote_style
    assert _preserve_quote_style('"hi"', '“hi”', 'say "x"') == 'say “x”'


def test_bash_merges_streams_and_preserves_indentation():
    from tools.builtin.BashTool.BashTool import BashTool
    b = BashTool(load_config(cwd=Path("/tmp")))
    r = _run(b.execute(ToolInvocation(
        params={"command": "printf '    indented\\n'; echo ERRX >&2"}, cwd=Path("/tmp"))))
    assert "    indented" in r.output and "ERRX" in r.output


def test_bash_timeout_preserves_partial_output():
    from tools.builtin.BashTool.BashTool import BashTool, _format_duration
    assert _format_duration(300) == "0s" and _format_duration(120000) == "2m"
    assert _format_duration(2000) == "2s"
    b = BashTool(load_config(cwd=Path("/tmp")))
    r = _run(b.execute(ToolInvocation(
        params={"command": "echo before; sleep 5", "timeout": 300}, cwd=Path("/tmp"))))
    assert not r.success
    assert "before" in r.output and "timed out" in r.output
    assert r.to_model_output().startswith("before")


def test_ensure_tool_result_pairing_synthesizes_missing():
    from context.manager import _ensure_tool_result_pairing
    msgs = [
        {"role": "assistant", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "X", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "Y", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
    ]
    out = _ensure_tool_result_pairing(msgs)
    answered = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
    assert {"a", "b"} <= answered


def test_exit_plan_mode_asks_even_in_bypass():
    from permissions.checker import PermissionChecker
    from core_types.permissions import PermissionMode, ToolPermissionContext, PermissionBehavior
    from tools.builtin.ExitPlanModeTool.ExitPlanModeTool import ExitPlanModeTool
    cfg = load_config(cwd=Path("/tmp"))
    ctx = ToolPermissionContext(mode=PermissionMode.BYPASS, working_directories=("/tmp",),
                                allow_rules={}, deny_rules={}, ask_rules={})
    r = _run(PermissionChecker(ctx).check(ExitPlanModeTool(cfg), {"plan": "x"}))
    assert r.behavior == PermissionBehavior.ASK and r.message == "Exit plan mode?"


def test_working_dir_containment_symlink_escape_denied():
    """EVERY path in the symlink set must be inside the working dir: a symlink
    living inside cwd but pointing outside is NOT contained (its resolved form
    fails), while a symlink pointing inside is. Regression: an any-path-inside
    variant wrongly auto-allowed reads through escape symlinks."""
    from permissions.filesystem import path_in_working_directory as inwd
    with tempfile.TemporaryDirectory() as t:
        t = os.path.realpath(t)
        (Path(t) / "x.py").write_text("ok")
        os.symlink("/etc/passwd", f"{t}/leak")
        os.symlink(f"{t}/x.py", f"{t}/oklink")
        os.symlink("/etc", f"{t}/dirlink")
        assert inwd(f"{t}/x.py", (t,)) is True
        assert inwd(f"{t}/oklink", (t,)) is True
        assert inwd(f"{t}/leak", (t,)) is False
        assert inwd(f"{t}/dirlink/passwd", (t,)) is False
        assert inwd(f"{t}/../etc/passwd", (t,)) is False


def test_post_compact_rereads_recent_files():
    from tools.builtin.shared import readFileState
    from services.compact.auto_compact import _post_compact_file_attachments
    readFileState.clear()
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "r.py"; f.write_text("FRESH_XYZ = 1\n")
        readFileState.record_read(f, f.read_text())
        atts = _post_compact_file_attachments()
        assert any("FRESH_XYZ" in m.content for m in atts)
