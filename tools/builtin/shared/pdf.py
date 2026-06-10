"""
PDF page rendering — Claude Code parity (src/utils/pdf.ts + utils/pdfUtils.ts).

Claude Code does NOT extract PDF text. It reads PDFs as VISION: pages are
rendered to JPEG images with `pdftoppm` (poppler-utils) and fed to the model as
image blocks; the page count is read with `pdfinfo`. This module mirrors CC's
getPDFPageCount / extractPDFPages / parsePDFPageRange.

We always render to page-images. That is CC's `!isPDFSupported()` branch, and it
is the only branch that works for non-Anthropic providers: OpenAI/Azure have no
native `application/pdf` document block, only image inputs. CC's small-PDF path
(inline `application/pdf` document block, Anthropic-native) is intentionally not
ported — it is unreachable without a live Anthropic provider, which this build
does not have, so porting it would be untested dead code.
"""

from __future__ import annotations

import base64
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

# Claude Code constants (src/constants/apiLimits.ts).
PDF_MAX_PAGES_PER_READ = 20  # max pages per request via the `pages` param
PDF_AT_MENTION_INLINE_THRESHOLD = 10  # whole-PDF read only if <= this many pages
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024  # 100 MB hard cap on extraction
PDF_RENDER_DPI = 100  # CC: `pdftoppm -r 100`


class PDFRenderError(Exception):
    """pdftoppm/pdfinfo unavailable, or the PDF could not be rendered."""


def pdf_render_available() -> bool:
    """True when the poppler binary CC relies on (`pdftoppm`) is installed."""
    return shutil.which("pdftoppm") is not None


def parse_pdf_page_range(pages: str) -> tuple[int, float] | None:
    """Parse a page range into (first_page, last_page), 1-indexed inclusive.

    Mirrors CC parsePDFPageRange exactly:
      "5"    -> (5, 5)
      "1-10" -> (1, 10)
      "3-"   -> (3, inf)   open-ended
    Returns None on invalid input (non-numeric, zero, inverted range). No
    comma lists — a single contiguous range only, matching CC.
    """
    trimmed = pages.strip()
    if not trimmed:
        return None

    # "N-" open-ended range
    if trimmed.endswith("-"):
        try:
            first = int(trimmed[:-1])
        except ValueError:
            return None
        if first < 1:
            return None
        return (first, math.inf)

    dash = trimmed.find("-")
    if dash == -1:
        # Single page: "5"
        try:
            page = int(trimmed)
        except ValueError:
            return None
        if page < 1:
            return None
        return (page, page)

    # Range: "1-10"
    try:
        first = int(trimmed[:dash])
        last = int(trimmed[dash + 1:])
    except ValueError:
        return None
    if first < 1 or last < 1 or last < first:
        return None
    return (first, last)


def page_range_count(first_page: int, last_page: float) -> float:
    """Number of pages a parsed range covers (inf when open-ended), CC parity."""
    return math.inf if last_page == math.inf else last_page - first_page + 1


def get_pdf_page_count(path: Path) -> int | None:
    """Page count via `pdfinfo` (CC getPDFPageCount). None if undeterminable."""
    if shutil.which("pdfinfo") is None:
        return None
    try:
        proc = subprocess.run(
            ["pdfinfo", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def render_pdf_pages(
    path: Path,
    first_page: int | None = None,
    last_page: float | None = None,
) -> list[dict[str, str]]:
    """Render PDF pages to base64 JPEGs via `pdftoppm` (CC extractPDFPages).

    Returns image attachments in page order:
        [{"media_type": "image/jpeg", "data": <base64>}, ...]
    Raises PDFRenderError with a CC-aligned message on any failure.
    """
    size = path.stat().st_size
    if size == 0:
        raise PDFRenderError(f"PDF file is empty: {path}")
    if size > PDF_MAX_EXTRACT_SIZE:
        cap_mb = PDF_MAX_EXTRACT_SIZE // (1024 * 1024)
        raise PDFRenderError(
            f"PDF file exceeds maximum allowed size for extraction ({cap_mb} MB)."
        )
    if not pdf_render_available():
        raise PDFRenderError(
            "pdftoppm is not installed. Install poppler-utils (e.g. "
            "`brew install poppler` or `apt-get install poppler-utils`) to "
            "enable PDF page rendering."
        )

    with tempfile.TemporaryDirectory(prefix="pdf-") as outdir:
        prefix = str(Path(outdir) / "page")
        # CC: `pdftoppm -jpeg -r 100 [-f first] [-l last] <file> <prefix>`
        args = ["pdftoppm", "-jpeg", "-r", str(PDF_RENDER_DPI)]
        if first_page:
            args += ["-f", str(first_page)]
        if last_page is not None and last_page != math.inf:
            args += ["-l", str(int(last_page))]
        args += [str(path), prefix]

        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=120
            )
        except (subprocess.SubprocessError, OSError) as e:
            raise PDFRenderError(f"pdftoppm failed: {e}") from e

        if proc.returncode != 0:
            err = (proc.stderr or "").lower()
            if "password" in err:
                raise PDFRenderError(
                    "PDF is password-protected. Please provide an unprotected version."
                )
            if any(w in err for w in ("damaged", "corrupt", "invalid")):
                raise PDFRenderError("PDF file is corrupted or invalid.")
            raise PDFRenderError(f"pdftoppm failed: {proc.stderr.strip()}")

        images: list[dict[str, str]] = []
        for jpg in sorted(Path(outdir).glob("*.jpg")):
            data = base64.b64encode(jpg.read_bytes()).decode("ascii")
            images.append({"media_type": "image/jpeg", "data": data})

    if not images:
        raise PDFRenderError(
            "pdftoppm produced no output pages. The PDF may be invalid."
        )
    return images
