"""
FileRead PDF = Claude Code parity: PDFs are read as VISION (pages rendered to
JPEG images via pdftoppm), never as extracted text and never dumped as raw
bytes. These exercise the real pdftoppm/pdfinfo path (poppler-utils).
"""
import asyncio
import math
import tempfile
from pathlib import Path

import pytest

from config.config import Config
from tools.base import ToolInvocation
from tools.builtin.FileReadTool.FileReadTool import FileReadTool
from tools.builtin.shared.pdf import parse_pdf_page_range, pdf_render_available

# Skip the rendering tests if poppler isn't installed (parse tests still run).
requires_poppler = pytest.mark.skipif(
    not pdf_render_available(), reason="poppler-utils (pdftoppm) not installed"
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_pdf(path: Path, n_pages: int = 1, text: str | None = None) -> None:
    """Write a minimal valid multi-page PDF with no external dependencies.

    Objects: 1=Catalog, 2=Pages, 3=Font, then per page a Page + Contents obj.
    Each page draws `text` (if given) so a renderer/vision model can read it.
    """
    font_obj = 3
    obj_defs: dict[int, bytes] = {}
    page_ids: list[int] = []

    next_id = 4
    for _ in range(n_pages):
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        stream = (
            f"BT /F1 24 Tf 36 700 Td ({text}) Tj ET".encode("latin-1")
            if text
            else b""
        )
        obj_defs[content_id] = (
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
        obj_defs[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
            % (content_id, font_obj)
        )

    obj_defs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    obj_defs[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n_pages)
    obj_defs[font_obj] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    max_id = next_id - 1
    for oid in range(1, max_id + 1):
        offsets[oid] = len(out)
        out += b"%d 0 obj\n" % oid + obj_defs[oid] + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (max_id + 1)
    out += b"0000000000 65535 f \n"
    for oid in range(1, max_id + 1):
        out += b"%010d 00000 n \n" % offsets[oid]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        max_id + 1,
        xref_pos,
    )
    path.write_bytes(bytes(out))


# ── pure parse_pdf_page_range (CC parity) ───────────────────────────────────

def test_parse_page_range():
    assert parse_pdf_page_range("5") == (5, 5)
    assert parse_pdf_page_range("1-10") == (1, 10)
    first, last = parse_pdf_page_range("3-")
    assert first == 3 and last == math.inf
    # invalid: zero, inverted, non-numeric, empty
    assert parse_pdf_page_range("0") is None
    assert parse_pdf_page_range("5-2") is None
    assert parse_pdf_page_range("abc") is None
    assert parse_pdf_page_range("") is None


# ── rendering path (real pdftoppm) ──────────────────────────────────────────

@requires_poppler
def test_whole_small_pdf_renders_to_images():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        pdf = cwd / "doc.pdf"
        _make_pdf(pdf, 2, text="HELLO")
        cfg = Config()
        cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(
            ToolInvocation(params={"file_path": str(pdf)}, cwd=cwd)
        ))
        assert r.success
        assert r.metadata.get("pdf") is True
        assert r.metadata.get("pages_read") == 2
        assert len(r.images) == 2
        assert all(img["media_type"] == "image/jpeg" for img in r.images)
        assert all(img["data"] for img in r.images)
        assert "%PDF" not in (r.output or "")  # never dump raw PDF bytes


@requires_poppler
def test_pages_range_renders_subset():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        pdf = cwd / "doc.pdf"
        _make_pdf(pdf, 5)
        cfg = Config()
        cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(
            ToolInvocation(params={"file_path": str(pdf), "pages": "1-2"}, cwd=cwd)
        ))
        assert r.success
        assert r.metadata.get("pages_read") == 2
        assert len(r.images) == 2


@requires_poppler
def test_too_many_pages_without_range_errors():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        pdf = cwd / "big.pdf"
        _make_pdf(pdf, 12)  # > PDF_AT_MENTION_INLINE_THRESHOLD (10)
        cfg = Config()
        cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(
            ToolInvocation(params={"file_path": str(pdf)}, cwd=cwd)
        ))
        assert not r.success
        assert "too many" in (r.error or "").lower()
        assert "pages parameter" in (r.error or "")


def test_range_exceeds_max_errors():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        pdf = cwd / "doc.pdf"
        _make_pdf(pdf, 1)
        cfg = Config()
        cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(
            ToolInvocation(params={"file_path": str(pdf), "pages": "1-25"}, cwd=cwd)
        ))
        assert not r.success
        assert "exceeds maximum of 20" in (r.error or "")


def test_invalid_range_errors():
    with tempfile.TemporaryDirectory() as t:
        cwd = Path(t)
        pdf = cwd / "doc.pdf"
        _make_pdf(pdf, 1)
        cfg = Config()
        cfg.cwd = cwd
        r = _run(FileReadTool(cfg).execute(
            ToolInvocation(params={"file_path": str(pdf), "pages": "abc"}, cwd=cwd)
        ))
        assert not r.success
        assert "Invalid pages parameter" in (r.error or "")
