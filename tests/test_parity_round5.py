"""
Round-5 parity regressions — divergences found by re-auditing subsystems
previously (over-)marked "verified". Each test pins a specific behavior.
"""
import asyncio
import tempfile
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.GrepTool.GrepTool import _apply_head_limit, _DEFAULT_HEAD_LIMIT, _sort_by_mtime
from tools.builtin.GlobTool.GlobTool import GlobTool, _GLOB_MAX_RESULTS
from tools.builtin.FileReadTool.FileReadTool import FileReadTool
from tools.builtin.WebSearchTool.WebSearchTool import WebSearchTool
from tools.builtin.NotebookEditTool.NotebookEditTool import NotebookEditTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_grep_apply_head_limit_default_250():
    items = list(range(300))
    out, applied = _apply_head_limit(items, None)
    assert len(out) == _DEFAULT_HEAD_LIMIT == 250
    assert applied == 250


def test_grep_apply_head_limit_zero_unlimited():
    items = list(range(300))
    out, applied = _apply_head_limit(items, 0)
    assert len(out) == 300
    assert applied is None


def test_grep_apply_head_limit_no_truncation_no_applied():
    out, applied = _apply_head_limit(list(range(10)), None)
    assert len(out) == 10 and applied is None


def test_grep_vcs_exclusion_has_six_dirs():
    from tools.builtin.GrepTool.GrepTool import _VCS_DIRECTORIES_TO_EXCLUDE
    assert set(_VCS_DIRECTORIES_TO_EXCLUDE) == {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}


def test_grep_sort_by_mtime_newest_first():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        a, b = d / "a.txt", d / "b.txt"
        a.write_text("x")
        b.write_text("x")
        import os
        os.utime(a, (1000, 1000))
        os.utime(b, (2000, 2000))
        ordered = _sort_by_mtime([str(a), str(b)])
        assert ordered[0] == str(b)


def test_glob_caps_at_100():
    assert _GLOB_MAX_RESULTS == 100
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        for i in range(150):
            (d / f"f{i:03}.txt").write_text("x")
        cfg = Config(); cfg.cwd = d
        r = _run(GlobTool(cfg).execute(
            ToolInvocation(params={"pattern": "*.txt", "path": str(d)}, cwd=d)))
        assert r.success
        lines = [ln for ln in r.output.splitlines() if ln.endswith(".txt")]
        assert len(lines) == 100
        assert "truncated" in r.output.lower()


def test_bash_timeout_milliseconds():
    from tools.builtin.BashTool.BashTool import ShellParams, _DEFAULT_TIMEOUT_MS
    assert _DEFAULT_TIMEOUT_MS == 120_000
    p = ShellParams(command="echo hi", timeout=120_000)
    assert p.timeout == 120_000


def test_bash_runs_with_ms_timeout():
    with tempfile.TemporaryDirectory() as t:
        cfg = Config(); cfg.cwd = Path(t)
        r = _run(BashToolExec(cfg, "echo parity", Path(t)))
        assert r.success
        assert "parity" in r.output


async def BashToolExec(cfg, cmd, cwd):
    from tools.builtin.BashTool.BashTool import BashTool
    return await BashTool(cfg).execute(
        ToolInvocation(params={"command": cmd, "timeout": 5000}, cwd=cwd))


def test_bash_interpret_exit_code():
    from tools.builtin.BashTool.BashTool import _interpret_exit_code
    assert _interpret_exit_code("echo x | grep zzz", 1) == (False, "No matches found")
    assert _interpret_exit_code("grep zzz f", 2)[0] is True
    assert _interpret_exit_code("diff a b", 1) == (False, "Files differ")
    assert _interpret_exit_code("test -f x", 1) == (False, "Condition is false")
    assert _interpret_exit_code("ls /nope", 2)[0] is True
    assert _interpret_exit_code("echo ok", 0) == (False, None)


def test_bash_grep_no_match_is_success():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        r = _run(BashToolExec(_cfg(cwd), "echo hello | grep zzz", cwd))
        assert r.success
        assert r.output.strip() == ""


def test_bash_real_failure_still_errors():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        r = _run(BashToolExec(_cfg(cwd), "ls /definitely/not/here", cwd))
        assert not r.success


def test_read_line_number_tab_format():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        f = d / "x.txt"
        f.write_text("alpha\nbeta\n")
        cfg = Config(); cfg.cwd = d
        r = _run(FileReadTool(cfg).execute(
            ToolInvocation(params={"file_path": str(f)}, cwd=d)))
        assert r.success
        assert "1\talpha" in r.output
        assert "|alpha" not in r.output


def test_websearch_rejects_both_domains():
    cfg = Config(); cfg.cwd = Path(".")
    r = _run(WebSearchTool(cfg).execute(ToolInvocation(
        params={"query": "parity test", "allowed_domains": ["a.com"], "blocked_domains": ["b.com"]},
        cwd=Path("."))))
    assert not r.success
    assert "both" in (r.error or "").lower()


def _make_nb(path: Path):
    import json
    nb = {
        "cells": [
            {"cell_type": "code", "id": "c0", "source": "old", "metadata": {},
             "execution_count": 7, "outputs": [{"output_type": "stream", "text": "stale"}]},
        ],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb))


def test_notebook_replace_clears_outputs_and_string_source():
    import json
    from tools.builtin.shared.readFileState import record_read
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); nb = d / "n.ipynb"; _make_nb(nb)
        cfg = Config(); cfg.cwd = d
        record_read(nb, nb.read_text())
        r = _run(NotebookEditTool(cfg).execute(ToolInvocation(
            params={"notebook_path": str(nb), "cell_number": 0,
                    "new_source": "new\ncode", "edit_mode": "replace"}, cwd=d)))
        assert r.success
        cell = json.loads(nb.read_text())["cells"][0]
        assert cell["source"] == "new\ncode"
        assert cell["outputs"] == []
        assert cell["execution_count"] is None


def test_notebook_insert_goes_after_referenced_cell():
    import json
    from tools.builtin.shared.readFileState import record_read
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); nb = d / "n.ipynb"; _make_nb(nb)
        cfg = Config(); cfg.cwd = d
        record_read(nb, nb.read_text())
        r = _run(NotebookEditTool(cfg).execute(ToolInvocation(
            params={"notebook_path": str(nb), "cell_number": 0,
                    "new_source": "inserted", "edit_mode": "insert",
                    "cell_type": "markdown"}, cwd=d)))
        assert r.success
        cells = json.loads(nb.read_text())["cells"]
        assert len(cells) == 2
        assert cells[1]["source"] == "inserted"


def test_exit_plan_mode_requires_plan_mode():
    from tools.builtin.ExitPlanModeTool.ExitPlanModeTool import ExitPlanModeTool
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        cfg = Config(); cfg.cwd = d
        r = _run(ExitPlanModeTool(cfg).execute(ToolInvocation(params={}, cwd=d)))
        assert not r.success
        assert "not in plan mode" in (r.error or "").lower()


def test_agent_disallowed_tools_constant():
    from tools.builtin.AgentTool.AgentTool import _ALL_AGENT_DISALLOWED_TOOLS
    assert {"EnterPlanMode", "ExitPlanMode", "Agent", "AskUserQuestion"} <= _ALL_AGENT_DISALLOWED_TOOLS


def _cfg(cwd):
    c = Config(); c.cwd = cwd
    return c


def _edit(cwd, **params):
    from tools.builtin.FileEditTool.FileEditTool import FileEditTool
    return _run(FileEditTool(_cfg(cwd)).execute(
        ToolInvocation(params=params, cwd=cwd)))


def _read(cwd, path):
    return _run(FileReadTool(_cfg(cwd)).execute(
        ToolInvocation(params={"file_path": str(path)}, cwd=cwd)))


def test_edit_noop_rejected_with_cc_message():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "x.txt"; f.write_text("hello\n")
        _read(cwd, f)
        r = _edit(cwd, file_path=str(f), old_string="hello", new_string="hello")
        assert not r.success
        assert "No changes to make" in (r.error or "")


def test_edit_not_found_cc_message():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "x.txt"; f.write_text("hello\n")
        _read(cwd, f)
        r = _edit(cwd, file_path=str(f), old_string="ZZZ", new_string="q")
        assert not r.success
        assert r.error.startswith("String to replace not found in file.")
        assert "String: ZZZ" in r.error


def test_edit_multiple_matches_cc_message():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "x.txt"; f.write_text("a\na\na\n")
        _read(cwd, f)
        r = _edit(cwd, file_path=str(f), old_string="a", new_string="b")
        assert not r.success
        assert "Found 3 matches of the string to replace" in r.error
        assert "replace_all is false" in r.error


def test_persist_tool_result_writes_context_and_jsonl():
    from query.loop import _persist_tool_result
    seen = {}

    class FakeCtx:
        def add_tool_result(self, tool_call_id, content):
            seen["ctx"] = (tool_call_id, content)

    class FakeSession:
        def __init__(self):
            self.context_manager = FakeCtx()
        def save_message(self, **kw):
            seen["save"] = kw

    _persist_tool_result(FakeSession(), "call-1", "Permission denied", "Bash", 3)
    assert seen["ctx"] == ("call-1", "Permission denied")
    assert seen["save"]["role"] == "tool"
    assert seen["save"]["tool_call_id"] == "call-1"
    assert seen["save"]["content"] == "Permission denied"


def test_truncation_string_classifies_as_max_output():
    """The loop sets this exact string on a length/max_tokens finish_reason; it
    must classify as MAX_OUTPUT_TOKENS so the recovery branch fires (was dead
    code — finish_reason was never inspected)."""
    from client.llm_client import classify_error, ErrorKind
    s = "max_output_tokens: response truncated at the output token limit"
    assert classify_error(s) == ErrorKind.MAX_OUTPUT_TOKENS


def test_token_usage_tracks_cache_creation():
    from client.response import TokenUsage
    a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                   cached_tokens=3, cache_creation_tokens=7)
    b = TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2,
                   cached_tokens=1, cache_creation_tokens=1)
    s = a + b
    assert s.cache_creation_tokens == 8
    assert s.cached_tokens == 4


def test_bash_preserves_leading_indentation_with_stderr():
    """Regression: the old `.strip()` ate the first stdout line's indentation
    whenever stderr was present. strips only leading blank lines."""
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        r = _run(BashToolExec(_cfg(cwd), "printf '    indented\\n    l2\\n'; echo E 1>&2", cwd))
        assert r.output.startswith("    indented")


def test_notebook_cell_n_id_fallback():
    """parseCellId: a 'cell-N' id resolves to index N when no literal id."""
    from tools.builtin.NotebookEditTool.NotebookEditTool import NotebookEditParams
    p = NotebookEditParams(notebook_path="x.ipynb", cell_id="cell-2",
                           new_source="y", edit_mode="replace")
    assert p.resolve_index([{"id": "a"}, {"id": "b"}, {"id": "c"}]) == 2


def test_edit_curly_quote_normalization():
    """findActualString: straight quotes in old_string match curly quotes in
    the file (and vice-versa), and the file's quote style is preserved."""
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "q.txt"
        f.write_text("greet = ‘hi’\n")
        _read(cwd, f)
        r = _edit(cwd, file_path=str(f), old_string="'hi'", new_string="'bye'")
        assert r.success


def test_read_empty_file_cc_message():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); f = cwd / "e.txt"; f.write_text("")
        r = _run(FileReadTool(_cfg(cwd)).execute(
            ToolInvocation(params={"file_path": str(f)}, cwd=cwd)))
        assert r.success
        assert "contents are empty" in r.output
        assert "system-reminder" in r.output


def test_read_not_found_names_cwd():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        r = _run(FileReadTool(_cfg(cwd)).execute(
            ToolInvocation(params={"file_path": str(cwd / "nope.txt")}, cwd=cwd)))
        assert not r.success
        assert "does not exist" in (r.error or "")
        assert str(cwd) in (r.error or "")
