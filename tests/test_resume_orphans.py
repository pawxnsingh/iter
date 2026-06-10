"""Session resume must filter incomplete (unanswered) tool_calls.

Regression: resuming a transcript ending with an assistant tool_call that has
no following tool result leaves an orphan tool_call; the next model API call
rejects it ('tool_call without tool_result'). CC: filterIncompleteToolCalls.
"""
import asyncio
import tempfile
from pathlib import Path

from config.config import Config
from agent.session import Session
from session.storage import SessionStorage
from commands import _do_resume


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_resume_drops_unanswered_tool_calls():
    async def go():
        with tempfile.TemporaryDirectory() as t:
            cwd = Path(t); cfg = Config(); cfg.cwd = cwd
            sess = Session(cfg); await sess.initialize()
            sess.save_message(role="user", content="hello", turn=1)
            sess.save_message(role="assistant", content="hi", turn=1)
            sess.save_message(role="user", content="read x and y", turn=2)
            sess.save_message(role="assistant", content="", tool_calls=[
                {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
            ], turn=2)
            sess.save_message(role="tool", content="file content", tool_call_id="c1", turn=2)
            # c2 never answered (interrupted)

            metas = SessionStorage.list_sessions(str(cwd))
            sess2 = Session(cfg); await sess2.initialize()
            _do_resume(metas[0], {"config": cfg, "context_manager": sess2.context_manager})

            msgs = sess2.context_manager._messages
            tool_ids = {m.tool_call_id for m in msgs if m.role == "tool"}
            all_calls = [tc.get("id") for m in msgs if m.role == "assistant" and m.tool_calls
                         for tc in m.tool_calls]
            orphans = [c for c in all_calls if c not in tool_ids]
            assert orphans == []        # no unanswered tool_calls remain
            assert "c1" in all_calls     # the answered call is preserved

    _run(go())
