"""FileRead CC parity: PDFs are extracted as text (not dumped as raw bytes); pages honored."""
import asyncio
import tempfile
from pathlib import Path

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.FileReadTool.FileReadTool import FileReadTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_pdf(path: Path, n_pages=2):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        w.write(f)


def test_pdf_extracted_not_dumped():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); pdf = cwd / "doc.pdf"; _make_pdf(pdf, 2)
        cfg = Config(); cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(ToolInvocation(params={"file_path": str(pdf)}, cwd=cwd)))
        assert r.success
        assert r.metadata.get("pdf") is True
        assert r.metadata.get("total_pages") == 2
        assert "%PDF" not in (r.output or "")  # never dump raw PDF source
        assert "--- Page 1 ---" in r.output


def test_pdf_pages_range():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t); pdf = cwd / "doc.pdf"; _make_pdf(pdf, 5)
        cfg = Config(); cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(ToolInvocation(
            params={"file_path": str(pdf), "pages": "1-2"}, cwd=cwd)))
        assert r.success
        assert r.metadata.get("pages_read") == 2


def test_page_range_parser():
    parse = FileReadTool._parse_page_range
    assert parse("1-3", 10) == [0, 1, 2]
    assert parse("3", 10) == [2]
    assert parse("1-2,5", 10) == [0, 1, 4]
