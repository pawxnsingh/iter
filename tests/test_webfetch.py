"""WebFetch CC parity: requires url+prompt, SSRF blocks internal hosts."""
import asyncio
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.WebFetchTool.WebFetchTool import WebFetchTool, WebFetchParams


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_schema_requires_prompt():
    with pytest.raises(ValidationError):
        WebFetchParams(url="https://example.com")  # no prompt
    # url+prompt valid
    p = WebFetchParams(url="https://example.com", prompt="summarize")
    assert p.prompt == "summarize"


def test_ssrf_blocks_localhost():
    cfg = Config(); cfg.cwd = Path(tempfile.mkdtemp())
    r = _run(WebFetchTool(cfg).execute(ToolInvocation(
        params={"url": "http://localhost:8080/", "prompt": "x"}, cwd=cfg.cwd)))
    assert not r.success and "internal" in (r.error or "").lower()


def test_rejects_non_http_scheme():
    cfg = Config(); cfg.cwd = Path(tempfile.mkdtemp())
    r = _run(WebFetchTool(cfg).execute(ToolInvocation(
        params={"url": "ftp://example.com/x", "prompt": "x"}, cwd=cfg.cwd)))
    assert not r.success


def test_html_to_text_strips_tags():
    cfg = Config(); cfg.cwd = Path(tempfile.mkdtemp())
    t = WebFetchTool(cfg)._html_to_text(
        "<html><head><style>x{}</style></head><body><h1>Hi</h1><p>world &amp; more</p></body></html>"
    )
    assert "Hi" in t and "world & more" in t
    assert "<" not in t and "style" not in t
