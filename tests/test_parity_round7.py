"""
Round-7 parity regressions — the remaining backlog closed to drive divergence
to zero: retry policy, per-message tool-result budget, abort propagation,
hook base-context.
"""
import asyncio
import tempfile
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _Err(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


def test_retry_covers_5xx_429_overloaded_connection():
    from client.llm_client import _should_retry_request
    assert _should_retry_request(_Err("rate limit", 429))[0]
    assert _should_retry_request(_Err("server error", 500))[0]
    assert _should_retry_request(_Err("bad gateway", 502))[0]
    assert _should_retry_request(_Err("unavailable", 503))[0]
    assert _should_retry_request(_Err("overloaded_error", 529))[0]
    assert _should_retry_request(_Err("connection reset"))[0]
    assert _should_retry_request(_Err("request timeout", 408))[0]
    assert not _should_retry_request(_Err("bad request", 400))[0]
    assert not _should_retry_request(_Err("not found", 404))[0]


def test_retry_honors_retry_after_header():
    from client.llm_client import _should_retry_request

    class Resp:
        headers = {"retry-after": "7"}

    class E(Exception):
        status_code = 429
        response = Resp()

    should, after = _should_retry_request(E("rate limit"))
    assert should and after == 7.0


def test_tool_result_budget_is_per_message_not_aggregate():
    from context.manager import ContextManager, MessageItem
    cm = ContextManager.__new__(ContextManager)
    big = "x" * 150_000
    cm._messages = [
        MessageItem(role="tool", content=big, tool_call_id="a"),
        MessageItem(role="tool", content=big, tool_call_id="b"),
    ]
    cm.apply_tool_result_budget()
    assert len(cm._messages[0].content) == 150_000
    assert len(cm._messages[1].content) == 150_000


def test_tool_result_budget_truncates_single_oversize():
    from context.manager import ContextManager, MessageItem
    cm = ContextManager.__new__(ContextManager)
    cm._messages = [MessageItem(role="tool", content="y" * 250_000, tool_call_id="a")]
    cm.apply_tool_result_budget()
    assert len(cm._messages[0].content) < 250_000
    assert "truncated" in cm._messages[0].content


class _Abort:
    def __init__(self):
        self.aborted = False


def test_bash_cancels_on_abort():
    from tools.builtin.BashTool.BashTool import BashTool
    with tempfile.TemporaryDirectory() as t:
        cfg = Config(); cfg.cwd = Path(t)
        ab = _Abort()

        async def go():
            inv = ToolInvocation(
                params={"command": "sleep 30", "timeout": 60000},
                cwd=Path(t), abort_controller=ab,
            )
            task = asyncio.ensure_future(BashTool(cfg).execute(inv))
            await asyncio.sleep(0.3)
            ab.aborted = True
            return await task

        r = _run(go())
        assert not r.success
        assert "aborted before completion" in (r.error or r.output or "").lower()


def test_run_tools_drains_remaining_on_abort():
    """An aborted batch yields synthetic results for every call so no tool_use
    is orphaned (would 400 the next request)."""
    from services.tools.orchestration import run_tools, ToolCallRequest

    ab = _Abort(); ab.aborted = True
    calls = [ToolCallRequest(call_id="c1", name="Read", arguments={}),
             ToolCallRequest(call_id="c2", name="Read", arguments={})]

    async def collect():
        out = []
        async for r in run_tools(calls, {}, Path("."), ab):
            out.append(r)
        return out

    results = _run(collect())
    assert {r.call_id for r in results} == {"c1", "c2"}
    assert all(not r.result.success for r in results)


def test_event_hook_gets_base_context():
    from hooks.executor import HookExecutor
    from core_types.hooks import HookCommand
    ex = HookExecutor({"SessionStart": [HookCommand(command="true")]})
    ex.set_base_context(session_id="s1", cwd="/tmp", permission_mode="default")
    assert ex._base_context == {"session_id": "s1", "cwd": "/tmp", "permission_mode": "default"}
