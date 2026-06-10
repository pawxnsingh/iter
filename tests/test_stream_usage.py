"""Streamed OpenAI/Azure calls must request usage via stream_options.

Regression: without stream_options={"include_usage": True}, every streamed turn
reported usage=None and token accounting (budget + compaction thresholds)
silently fell back to a crude estimate ignoring prompt tokens.
"""
import asyncio
from types import SimpleNamespace as NS

from config.config import Config
from client.llm_client import LLMClient
from client.response import StreamEventType


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_stream_options_requested_and_usage_captured():
    captured = {}

    async def gen():
        # content chunk
        yield NS(usage=None, choices=[NS(delta=NS(content="hi", tool_calls=None), finish_reason=None)])
        # final usage-only chunk (empty choices), as OpenAI sends with include_usage
        yield NS(usage=NS(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                          prompt_tokens_details=NS(cached_tokens=3)),
                 choices=[])

    async def create(**kw):
        captured.update(kw)
        return gen()

    client = LLMClient(Config())
    fake = NS(chat=NS(completions=NS(create=create)))

    async def go():
        usage = None
        async for ev in client._openai_stream(fake_messages := [{"role": "user", "content": "x"}],
                                               tools=None, stream=True):
            if ev.type == StreamEventType.MESSAGE_COMPLETE:
                usage = ev.usage
        return usage

    # patch _get_client to return our fake
    client._get_client = lambda: fake
    usage = _run(go())

    assert captured.get("stream_options") == {"include_usage": True}
    assert usage is not None
    assert usage.prompt_tokens == 10 and usage.total_tokens == 15 and usage.cached_tokens == 3
