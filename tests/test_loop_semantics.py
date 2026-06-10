"""Core query-loop semantics (CC parity): parallel tool calls + no orphan results."""
import asyncio
import tempfile
from pathlib import Path

from client.response import StreamEvent, StreamEventType, TextDelta, ToolCall, TokenUsage
from config.config import Config
from agent.session import Session
from query.loop import query_loop


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _fake(script, capture):
    turns = list(script)

    async def chat(messages, tools=None, stream=True):
        capture["messages"] = messages
        evs = turns.pop(0) if turns else [
            StreamEvent(type=StreamEventType.TEXT_DELTA, text_delta=TextDelta(content="done")),
            StreamEvent(type=StreamEventType.MESSAGE_COMPLETE, finish_reason="stop", usage=TokenUsage(1, 1, 2)),
        ]
        for e in evs:
            yield e

    return chat


def _done(reason="stop"):
    return StreamEvent(type=StreamEventType.MESSAGE_COMPLETE, finish_reason=reason, usage=TokenUsage(1, 1, 2))


def test_parallel_tool_calls_all_execute():
    async def go():
        with tempfile.TemporaryDirectory() as t:
            cwd = Path(t)
            for n in "abc":
                (cwd / f"{n}.txt").write_text(n.upper() + "\n")
            cfg = Config(); cfg.cwd = cwd; cfg.max_turns = 5
            sess = Session(cfg); await sess.initialize()
            cap = {}
            sess.client.chat_completion = _fake([
                [StreamEvent(type=StreamEventType.TOOL_CALL_COMPLETED,
                             tool_call=ToolCall(call_id=f"c{i}", name="Read",
                                                arguments={"file_path": str(cwd / f'{n}.txt')}))
                 for i, n in enumerate("abc")] + [_done("tool_calls")],
                [StreamEvent(type=StreamEventType.TEXT_DELTA, text_delta=TextDelta(content="ok")), _done()],
            ], cap)
            done = []
            async for ev in query_loop(sess, cfg, permission_prompt_fn=None):
                if (ev.type.value if hasattr(ev.type, "value") else "") == "tool_call_complete":
                    done.append(ev.data.get("success"))
            await sess.client.close()
            assert done.count(True) == 3
            tool_ids = {m.get("tool_call_id") for m in cap["messages"] if isinstance(m, dict) and m.get("role") == "tool"}
            assert tool_ids == {"c0", "c1", "c2"}

    _run(go())


def test_no_orphan_tool_calls():
    """Unknown tool + invalid-args call still get tool results (API requires it)."""
    async def go():
        with tempfile.TemporaryDirectory() as t:
            cwd = Path(t); (cwd / "ok.txt").write_text("hi\n")
            cfg = Config(); cfg.cwd = cwd; cfg.max_turns = 5
            sess = Session(cfg); await sess.initialize()
            cap = {}
            sess.client.chat_completion = _fake([
                [StreamEvent(type=StreamEventType.TOOL_CALL_COMPLETED,
                             tool_call=ToolCall(call_id="c1", name="Read", arguments={"file_path": str(cwd / "ok.txt")})),
                 StreamEvent(type=StreamEventType.TOOL_CALL_COMPLETED,
                             tool_call=ToolCall(call_id="c2", name="NoSuchTool", arguments={})),
                 StreamEvent(type=StreamEventType.TOOL_CALL_COMPLETED,
                             tool_call=ToolCall(call_id="c3", name="Read", arguments={})),
                 _done("tool_calls")],
                [StreamEvent(type=StreamEventType.TEXT_DELTA, text_delta=TextDelta(content="ok")), _done()],
            ], cap)
            async for _ in query_loop(sess, cfg, permission_prompt_fn=None):
                pass
            await sess.client.close()
            tool_ids = {m.get("tool_call_id") for m in cap["messages"] if isinstance(m, dict) and m.get("role") == "tool"}
            assert tool_ids == {"c1", "c2", "c3"}

    _run(go())
