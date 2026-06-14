"""Tests for memory system."""

from memory.memdir import (
    MAX_ENTRYPOINT_LINES,
    truncate_entrypoint,
)


def test_no_truncation_needed():
    content = "line 1\nline 2\nline 3"
    result = truncate_entrypoint(content)
    assert result == content


def test_line_truncation():
    content = "\n".join(f"line {i}" for i in range(300))
    result = truncate_entrypoint(content)
    assert "WARNING" in result
    lines_before_warning = result.split("\n\n> WARNING")[0].split("\n")
    assert len(lines_before_warning) <= MAX_ENTRYPOINT_LINES + 1


def test_byte_truncation():
    content = "x" * 30_000
    result = truncate_entrypoint(content)
    assert "WARNING" in result
