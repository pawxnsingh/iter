"""Read-before-write + freshness enforcement (Claude Code readFileState parity).

CC FileEdit/FileWrite validateInput errorCode 6 (not read) and 7 (stale).
"""
import asyncio
import tempfile
import time
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.FileReadTool import FileReadTool
from tools.builtin.FileEditTool import FileEditTool
from tools.builtin.FileWriteTool import FileWriteTool
from tools.builtin.shared.readFileState import clear


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _cfg(cwd):
    c = Config()
    c.cwd = cwd
    return c


def test_edit_existing_without_read_blocks():
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "a.txt"; f.write_text("hello world\n")
        r = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "old_string": "world", "new_string": "mars"}, cwd=cwd)))
        assert not r.success
        assert "has not been read" in (r.error or "")


def test_read_then_edit_succeeds():
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "a.txt"; f.write_text("hello world\n")
        _run(FileReadTool(_cfg(cwd)).execute(ToolInvocation(params={"file_path": str(f)}, cwd=cwd)))
        r = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "old_string": "world", "new_string": "mars"}, cwd=cwd)))
        assert r.success
        assert f.read_text() == "hello mars\n"


def test_stale_edit_blocks_after_external_change():
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "a.txt"; f.write_text("hello world\n")
        _run(FileReadTool(_cfg(cwd)).execute(ToolInvocation(params={"file_path": str(f)}, cwd=cwd)))
        time.sleep(0.02)
        f.write_text("EXTERNALLY CHANGED\n")
        r = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "old_string": "CHANGED", "new_string": "X"}, cwd=cwd)))
        assert not r.success
        assert "modified since read" in (r.error or "")


def test_chained_edits_dont_trip_freshness():
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "a.txt"; f.write_text("a b c\n")
        _run(FileReadTool(_cfg(cwd)).execute(ToolInvocation(params={"file_path": str(f)}, cwd=cwd)))
        r1 = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "old_string": "a", "new_string": "X"}, cwd=cwd)))
        r2 = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "old_string": "b", "new_string": "Y"}, cwd=cwd)))
        assert r1.success and r2.success
        assert f.read_text() == "X Y c\n"


def test_create_new_file_needs_no_read():
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        r = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(cwd / "new.txt"), "old_string": "", "new_string": "fresh\n"}, cwd=cwd)))
        assert r.success
        assert (cwd / "new.txt").read_text() == "fresh\n"


def test_write_new_file_needs_no_read_but_overwrite_unread_blocks():
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        # new file: allowed
        r_new = _run(FileWriteTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(cwd / "w.txt"), "content": "v1\n"}, cwd=cwd)))
        assert r_new.success
        # overwrite our own just-written file: state is fresh → allowed
        r_over = _run(FileWriteTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(cwd / "w.txt"), "content": "v2\n"}, cwd=cwd)))
        assert r_over.success
        # overwrite a different existing file never read → blocked
        other = cwd / "o.txt"; other.write_text("x\n")
        r_block = _run(FileWriteTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(other), "content": "y\n"}, cwd=cwd)))
        assert not r_block.success
        assert "has not been read" in (r_block.error or "")


def test_limited_read_covering_whole_file_allows_edit():
    """A read with offset=1, limit>=file_lines has seen the whole file → editable.
    Regression: a limit alone was wrongly marking the read as a partial view,
    which blocked the subsequent edit ('File has not been read yet')."""
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "config.py"; f.write_text("DEBUG = False\nTIMEOUT = 30\n")
        # 2-line file read with limit=50 → full coverage
        _run(FileReadTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "offset": 1, "limit": 50}, cwd=cwd)))
        r = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "old_string": "DEBUG = False", "new_string": "DEBUG = True"}, cwd=cwd)))
        assert r.success
        assert f.read_text() == "DEBUG = True\nTIMEOUT = 30\n"


def test_genuinely_partial_read_still_blocks_edit():
    """A read that stops short of EOF is a partial view → edit still blocked."""
    clear()
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "big.py"
        f.write_text("\n".join(f"line{i}" for i in range(1, 101)) + "\n")
        # read only first 10 lines of a 100-line file → partial
        _run(FileReadTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "offset": 1, "limit": 10}, cwd=cwd)))
        r = _run(FileEditTool(_cfg(cwd)).execute(ToolInvocation(
            params={"file_path": str(f), "old_string": "line50", "new_string": "L50"}, cwd=cwd)))
        assert not r.success
        assert "has not been read" in (r.error or "")
