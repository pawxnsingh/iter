"""FileRead parity:.ipynb returns cells with outputs; pages param accepted."""
import asyncio
import json
import tempfile
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.FileReadTool.FileReadTool import FileReadTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _nb(cwd: Path) -> Path:
    nb = cwd / "demo.ipynb"
    nb.write_text(json.dumps({
        "cells": [
            {"id": "c1", "cell_type": "markdown", "source": "# Title\n", "outputs": [], "metadata": {}},
            {"id": "c2", "cell_type": "code", "source": "print(1+1)\n", "execution_count": 1,
             "metadata": {}, "outputs": [{"output_type": "stream", "name": "stdout", "text": "2\n"}]},
            {"id": "c3", "cell_type": "code", "source": "1/0\n", "metadata": {},
             "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero"}]},
        ],
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                     "language_info": {"name": "python"}},
        "nbformat": 4, "nbformat_minor": 5,
    }))
    return nb


def test_read_notebook_renders_cells():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); cfg = Config(); cfg.cwd = cwd
        nb = _nb(cwd)
        r = _run(FileReadTool(cfg).execute(ToolInvocation(params={"file_path": str(nb)}, cwd=cwd)))
        assert r.success
        assert "Cell [0]" in r.output and "Cell [2]" in r.output
        assert "# Title" in r.output
        assert "[stream:stdout] 2" in r.output
        assert "ZeroDivisionError" in r.output
        assert r.metadata.get("notebook") is True


def test_read_accepts_pages_param():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); cfg = Config(); cfg.cwd = cwd
        f = cwd / "x.txt"; f.write_text("hello\n")
        r = _run(FileReadTool(cfg).execute(ToolInvocation(params={"file_path": str(f), "pages": "1-5"}, cwd=cwd)))
        assert r.success
