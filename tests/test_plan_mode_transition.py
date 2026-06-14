"""EnterPlanMode mid-loop must actually gate subsequent write tools.

Regression: the loop updated config.permission_mode but kept a frozen
ToolPermissionContext, so plan mode never took effect. The loop now rebuilds
the checker on a set_permission_mode transition.
"""
import asyncio
import tempfile
from pathlib import Path

from client.response import StreamEvent, StreamEventType, TextDelta, ToolCall, TokenUsage
from config.config import Config
from agent.session import Session
from query.loop import query_loop


def _fake(script):
    turns = list(script)

    async def chat(messages, tools=None, stream=True):
        evs = turns.pop(0) if turns else [
            StreamEvent(type=StreamEventType.TEXT_DELTA, text_delta=TextDelta(content="done")),
            StreamEvent(type=StreamEventType.MESSAGE_COMPLETE, finish_reason="stop", usage=TokenUsage(1, 1, 2)),
        ]
        for e in evs:
            yield e

    return chat


def test_enter_plan_mode_blocks_subsequent_write():
    async def go():
        with tempfile.TemporaryDirectory() as t:
            cwd = Path(t)
            cfg = Config(); cfg.cwd = cwd; cfg.max_turns = 8
            sess = Session(cfg)
            await sess.initialize()
            sess.client.chat_completion = _fake([
                [StreamEvent(type=StreamEventType.TOOL_CALL_COMPLETED,
                             tool_call=ToolCall(call_id="c1", name="EnterPlanMode", arguments={})),
                 StreamEvent(type=StreamEventType.MESSAGE_COMPLETE, finish_reason="tool_calls", usage=TokenUsage(1, 1, 2))],
                [StreamEvent(type=StreamEventType.TOOL_CALL_COMPLETED,
                             tool_call=ToolCall(call_id="c2", name="Write",
                                                arguments={"file_path": str(cwd / "b.txt"), "content": "x"})),
                 StreamEvent(type=StreamEventType.MESSAGE_COMPLETE, finish_reason="tool_calls", usage=TokenUsage(1, 1, 2))],
                [StreamEvent(type=StreamEventType.TEXT_DELTA, text_delta=TextDelta(content="ok")),
                 StreamEvent(type=StreamEventType.MESSAGE_COMPLETE, finish_reason="stop", usage=TokenUsage(1, 1, 2))],
            ])
            results = []
            async for ev in query_loop(sess, cfg, permission_prompt_fn=None):
                t_ = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
                if t_ == "tool_call_complete":
                    results.append((ev.data.get("name"), ev.data.get("success")))
            await sess.client.close()
            writes = [ok for n, ok in results if n == "Write"]
            assert writes and writes[0] is False
            assert not (cwd / "b.txt").exists()

    asyncio.get_event_loop().run_until_complete(go())
