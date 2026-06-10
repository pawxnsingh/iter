"""Glob CC parity: results sorted by mtime, oldest first (rg --sort=modified)."""
import asyncio
import tempfile
import time
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.GlobTool.GlobTool import GlobTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_glob_sorts_by_mtime_oldest_first():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        for name in ["old.py", "mid.py", "new.py"]:
            (cwd / name).write_text("x\n")
            time.sleep(0.02)
        cfg = Config(); cfg.cwd = cwd
        r = _run(GlobTool(cfg).execute(ToolInvocation(params={"pattern": "*.py", "path": str(cwd)}, cwd=cwd)))
        order = [Path(l).name for l in r.output.splitlines() if l.strip()]
        assert order == ["old.py", "mid.py", "new.py"]
