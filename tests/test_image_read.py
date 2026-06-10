"""Image reading parity: FileRead emits base64 image attachment; message plumbing
converts to OpenAI image_url blocks and Anthropic image blocks."""
import asyncio
import struct
import tempfile
import zlib
from pathlib import Path

from config.config import Config
from context.manager import MessageItem
from tools.base import ToolInvocation
from tools.builtin.FileReadTool.FileReadTool import FileReadTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_png(path: Path, w=8, h=8, rgb=(0, 128, 255)):
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.write_bytes(png)


def test_filed_read_returns_image_attachment():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); img = cwd / "p.png"; _make_png(img)
        cfg = Config(); cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(ToolInvocation(params={"file_path": str(img)}, cwd=cwd)))
        assert r.success
        assert len(r.images) == 1
        assert r.images[0]["media_type"] == "image/png"
        assert r.images[0]["data"]  # base64 present
        assert r.metadata.get("image") is True


def test_message_emits_openai_image_block():
    m = MessageItem(role="user", content="look",
                    images=[{"media_type": "image/png", "data": "QUJD"}])
    d = m.to_dict()
    assert isinstance(d["content"], list)
    types = [b["type"] for b in d["content"]]
    assert "text" in types and "image_url" in types
    img_block = next(b for b in d["content"] if b["type"] == "image_url")
    assert img_block["image_url"]["url"].startswith("data:image/png;base64,QUJD")


def test_anthropic_image_remap():
    from client.llm_client import _remap_image_blocks_for_anthropic
    msg = {"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]}
    out = _remap_image_blocks_for_anthropic(msg)
    blocks = out["content"]
    img = next(b for b in blocks if b["type"] == "image")
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    assert img["source"]["data"] == "QUJD"
