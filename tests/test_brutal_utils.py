"""
Brutal tests for utility modules: utils/paths, utils/text, utils/errors,
utils/abort. Edge cases, unicode, empty inputs, error propagation.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_resolve_path_absolute_passthrough(tmp_path: Path) -> None:
    from utils.paths import resolve_path

    target = tmp_path / "a.txt"
    assert resolve_path("/elsewhere", target) == target.resolve()


def test_resolve_path_relative_joined_to_base(tmp_path: Path) -> None:
    from utils.paths import resolve_path

    r = resolve_path(str(tmp_path), "sub/file.txt")
    assert r == (tmp_path / "sub/file.txt").resolve() or r == tmp_path.resolve() / "sub/file.txt"


def test_resolve_path_accepts_string_and_path() -> None:
    from utils.paths import resolve_path

    r1 = resolve_path("/tmp", "x")
    r2 = resolve_path(Path("/tmp"), Path("x"))
    assert r1 == r2


def test_ensure_parent_directory_creates_nested(tmp_path: Path) -> None:
    from utils.paths import ensure_parent_directory

    target = tmp_path / "a" / "b" / "c" / "file.txt"
    ensure_parent_directory(target)
    assert target.parent.is_dir()


def test_ensure_parent_directory_idempotent(tmp_path: Path) -> None:
    from utils.paths import ensure_parent_directory

    target = tmp_path / "x" / "y.txt"
    ensure_parent_directory(target)
    ensure_parent_directory(target)


def test_is_binary_detects_null_byte(tmp_path: Path) -> None:
    from utils.paths import is_binary

    bin_file = tmp_path / "b.dat"
    bin_file.write_bytes(b"hello\x00world")
    text_file = tmp_path / "t.txt"
    text_file.write_text("hello world", encoding="utf-8")

    assert is_binary(bin_file) is True
    assert is_binary(text_file) is False


def test_is_binary_missing_file_returns_false(tmp_path: Path) -> None:
    from utils.paths import is_binary

    assert is_binary(tmp_path / "nonexistent") is False


def test_is_binary_empty_file(tmp_path: Path) -> None:
    from utils.paths import is_binary

    p = tmp_path / "empty"
    p.write_bytes(b"")
    assert is_binary(p) is False


def test_display_path_rel_returns_relative(tmp_path: Path) -> None:
    from utils.paths import display_path_rel_to_cwd

    child = tmp_path / "sub" / "file.txt"
    assert display_path_rel_to_cwd(str(child), tmp_path) == str(Path("sub/file.txt"))


def test_display_path_rel_outside_cwd_falls_back(tmp_path: Path) -> None:
    from utils.paths import display_path_rel_to_cwd

    result = display_path_rel_to_cwd("/etc/hosts", tmp_path)
    assert result == "/etc/hosts"


def test_display_path_rel_none_cwd() -> None:
    from utils.paths import display_path_rel_to_cwd

    assert display_path_rel_to_cwd("/etc/hosts", None) == "/etc/hosts"


def test_count_tokens_empty_string() -> None:
    from utils.text import count_tokens

    assert count_tokens("") == 0


def test_count_tokens_approx_matches_length() -> None:
    from utils.text import count_tokens

    n = count_tokens("hello world")
    assert 1 <= n <= 5


def test_count_tokens_unknown_model_falls_back() -> None:
    from utils.text import count_tokens

    n = count_tokens("some text", model="not-a-real-model")
    assert n > 0


def test_estimate_token_minimum_one() -> None:
    from utils.text import estimate_token

    assert estimate_token("") == 1
    assert estimate_token("a") == 1


def test_truncate_text_noop_when_under_limit() -> None:
    from utils.text import truncate_text

    text = "short"
    out = truncate_text(text, max_tokens=100)
    assert out == text


def test_truncate_text_adds_suffix_when_over_limit() -> None:
    from utils.text import truncate_text

    text = "line\n" * 500
    out = truncate_text(text, max_tokens=10)
    assert out.endswith("[truncated]")
    assert len(out) < len(text)


def test_truncate_text_preserve_lines_respects_newlines() -> None:
    from utils.text import truncate_text

    text = "line1\nline2\nline3\n" * 100
    out = truncate_text(text, max_tokens=20, preserve_lines=True)
    assert out.endswith("[truncated]")


def test_truncate_text_by_chars_when_no_line_fits() -> None:
    from utils.text import truncate_text

    text = "x" * 100_000
    out = truncate_text(text, max_tokens=10, preserve_lines=True)
    assert out.endswith("[truncated]")


def test_truncate_text_suffix_larger_than_budget() -> None:
    from utils.text import truncate_text

    out = truncate_text("x" * 1000, max_tokens=1, suffix="[truncated]")
    assert out.strip() == "[truncated]"


def test_agent_error_str_plain() -> None:
    from utils.errors import AgentError

    e = AgentError("boom")
    assert "boom" in str(e)


def test_agent_error_str_with_details() -> None:
    from utils.errors import AgentError

    e = AgentError("boom", details={"k": "v", "n": 42})
    s = str(e)
    assert "boom" in s and "k=v" in s and "n=42" in s


def test_agent_error_str_with_cause() -> None:
    from utils.errors import AgentError

    cause = ValueError("root")
    e = AgentError("boom", cause=cause)
    assert "caused by" in str(e)
    assert "root" in str(e)


def test_agent_error_to_dict_shape() -> None:
    from utils.errors import AgentError

    e = AgentError("oops", details={"a": 1})
    d = e.to_dict()
    assert d["type"] == "AgentError"
    assert d["message"] == "oops"
    assert d["details"] == {"a": 1}
    assert d["cause"] is None


def test_config_error_includes_file_and_key() -> None:
    from utils.errors import ConfigError

    e = ConfigError("bad", config_key="x.y", config_file="/t/c.toml")
    assert e.config_key == "x.y"
    assert e.config_file == "/t/c.toml"
    assert e.details.get("config_key") == "x.y"
    assert e.details.get("config_file") == "/t/c.toml"


@pytest.mark.asyncio
async def test_abort_controller_signals_and_reason() -> None:
    from utils.abort import AbortController

    ctrl = AbortController()
    assert ctrl.aborted is False
    ctrl.abort("user_interrupt")
    assert ctrl.aborted is True
    assert ctrl.reason == "user_interrupt"


def test_abort_controller_check_raises() -> None:
    from utils.abort import AbortController, AbortError

    ctrl = AbortController()
    ctrl.check()
    ctrl.abort()
    with pytest.raises(AbortError) as ei:
        ctrl.check()
    assert ei.value.reason == "aborted"


def test_abort_controller_abort_twice_is_noop() -> None:
    from utils.abort import AbortController

    ctrl = AbortController()
    ctrl.abort("first")
    ctrl.abort("second")
    assert ctrl.reason == "first"


@pytest.mark.asyncio
async def test_abort_cascades_to_children() -> None:
    from utils.abort import AbortController

    parent = AbortController()
    c1 = parent.create_child()
    c2 = parent.create_child()
    grandchild = c1.create_child()

    assert not c1.aborted
    parent.abort("parent")
    assert c1.aborted
    assert c2.aborted
    assert grandchild.aborted
    assert grandchild.reason == "parent"


@pytest.mark.asyncio
async def test_wait_for_abort_returns_reason() -> None:
    from utils.abort import AbortController

    ctrl = AbortController()

    async def trigger() -> None:
        await asyncio.sleep(0.01)
        ctrl.abort("time")

    task = asyncio.create_task(trigger())
    reason = await ctrl.wait_for_abort()
    assert reason == "time"
    await task


@pytest.mark.asyncio
async def test_wait_for_abort_already_aborted() -> None:
    from utils.abort import AbortController

    ctrl = AbortController()
    ctrl.abort("already")
    reason = await asyncio.wait_for(ctrl.wait_for_abort(), timeout=0.1)
    assert reason == "already"


def test_abort_error_preserves_reason() -> None:
    from utils.abort import AbortError

    e = AbortError("user_cancel")
    assert e.reason == "user_cancel"
    assert "user_cancel" in str(e)
