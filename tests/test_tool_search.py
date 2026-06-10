"""ToolSearch CC parity: select:<names> returns callable schemas; keyword search returns schemas."""
import asyncio
import tempfile
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.ToolSearchTool.ToolSearchTool import ToolSearchTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _search(q):
    cfg = Config(); cfg.cwd = Path(tempfile.mkdtemp())
    return _run(ToolSearchTool(cfg).execute(ToolInvocation(params={"query": q}, cwd=cfg.cwd)))


def test_select_returns_schemas():
    r = _search("select:Read,Edit")
    assert r.success
    assert "<functions>" in r.output
    assert '"name": "Read"' in r.output and '"name": "Edit"' in r.output
    assert "file_path" in r.output  # parameters present


def test_select_unknown_errors():
    r = _search("select:DefinitelyNotATool")
    assert not r.success


def test_keyword_search_returns_results():
    r = _search("search")
    assert r.success
    assert "Grep" in r.output or "WebSearch" in r.output
