"""Streaming tool-call accumulation: fragmented name/args, late id, parallel, missing id."""
import asyncio
from types import SimpleNamespace as NS

from config.config import Config
from client.llm_client import LLMClient


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _chunk(tcs=None, content=None, finish=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs), finish_reason=finish)], usage=None)


def _tc(index, id=None, name=None, args=None):
    fn = NS(name=name, arguments=args) if (name is not None or args is not None) else None
    return NS(index=index, id=id, function=fn)


async def _collect(chunks):
    async def gen():
        for c in chunks:
            yield c

    async def create(**kw):
        return gen()

    client = LLMClient(Config())
    fake = NS(chat=NS(completions=NS(create=create)))
    starts, completes = [], []
    async for ev in client._openai_stream_response(fake, {}):
        t = ev.type.value
        if t == "tool_call_start":
            starts.append((ev.tool_call_delta.call_id, ev.tool_call_delta.name))
        elif t == "tool_call_complete":
            completes.append((ev.tool_call.call_id, ev.tool_call.name, ev.tool_call.arguments))
    return starts, completes


def test_fragmented_name_and_args():
    s, c = _run(_collect([
        _chunk(tcs=[_tc(0, id="call_X", name="Re")]),
        _chunk(tcs=[_tc(0, name="ad")]),
        _chunk(tcs=[_tc(0, args='{"file_')]),
        _chunk(tcs=[_tc(0, args='path":"/x"}')]),
        _chunk(finish="tool_calls"),
    ]))
    assert c == [("call_X", "Read", {"file_path": "/x"})]
    assert len(s) == 1


def test_late_id():
    s, c = _run(_collect([
        _chunk(tcs=[_tc(0, name="Bash")]),
        _chunk(tcs=[_tc(0, id="call_Y")]),
        _chunk(tcs=[_tc(0, args='{"command":"ls"}')]),
        _chunk(finish="tool_calls"),
    ]))
    assert c[0][0] == "call_Y" and s[0][0] == "call_Y"


def test_parallel_calls_args_not_crossed():
    _, c = _run(_collect([
        _chunk(tcs=[_tc(0, id="c0", name="Read", args='{"file_path":')]),
        _chunk(tcs=[_tc(1, id="c1", name="Grep", args='{"pattern":')]),
        _chunk(tcs=[_tc(0, args='"/a"}')]),
        _chunk(tcs=[_tc(1, args='"x"}')]),
        _chunk(finish="tool_calls"),
    ]))
    c = sorted(c, key=lambda e: e[0])
    assert c[0] == ("c0", "Read", {"file_path": "/a"})
    assert c[1] == ("c1", "Grep", {"pattern": "x"})


def test_missing_id_synthesized_with_start():
    s, c = _run(_collect([
        _chunk(tcs=[_tc(0, name="Read", args='{"file_path":"/z"}')]),
        _chunk(finish="tool_calls"),
    ]))
    assert c[0][0].startswith("call_")
    assert len(s) == 1 and s[0][0] == c[0][0]
