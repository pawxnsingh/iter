"""Grep tool CC parity: output modes, filters, context, head_limit (ripgrep wrapper)."""
import asyncio
import tempfile
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.GrepTool.GrepTool import GrepTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _setup(cwd: Path):
    (cwd / "a.py").write_text("def foo():\n    return 1\n# foo again\n")
    (cwd / "b.js").write_text("function foo(){}\nconst x=foo();\n")
    (cwd / "c.txt").write_text("nothing here\n")


def _grep(cwd, **params):
    cfg = Config(); cfg.cwd = cwd
    return _run(GrepTool(cfg).execute(ToolInvocation(params={"path": str(cwd), **params}, cwd=cwd)))


def test_files_with_matches_default():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="foo")
        assert r.success
        assert "a.py" in r.output and "b.js" in r.output and "c.txt" not in r.output


def test_count_mode():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="foo", output_mode="count")
        assert r.success and any(":" in l for l in r.output.splitlines())


def test_content_mode():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="foo", output_mode="content")
        assert r.success and "foo" in r.output


def test_glob_filter():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="foo", glob="*.py")
        assert r.success and "a.py" in r.output and "b.js" not in r.output


def test_type_filter():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="foo", type="py")
        assert r.success and "a.py" in r.output and "b.js" not in r.output


def test_case_insensitive():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="FOO", case_insensitive=True, output_mode="content")
        assert r.success and "foo" in r.output.lower()


def test_head_limit_truncates():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="foo", output_mode="content", head_limit=1)
        assert r.success and "truncated" in r.output


def test_no_match():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); _setup(cwd)
        r = _grep(cwd, pattern="zzzznevermatch")
        assert r.success and "No matches" in r.output
