"""
Tool result storage — persist large results to disk.

When a tool result exceeds maxResultSizeChars, it's persisted to disk
and replaced with a preview + file path. This prevents large results
from consuming the entire context window.

Constants:
  DEFAULT_MAX_RESULT_SIZE_CHARS = 50,000 (fallback when a caller omits
      max_chars; the effective cap is per-tool via Tool.max_result_size_chars)
  MAX_TOOL_RESULT_BYTES = 400,000
  PREVIEW_SIZE_BYTES = 2,000
"""

from __future__ import annotations

from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000
MAX_TOOL_RESULT_BYTES = 400_000
PREVIEW_SIZE_BYTES = 2_000


def maybe_persist_large_result(
    output: str,
    tool_name: str,
    tool_use_id: str,
    session_dir: Path | None = None,
    max_chars: int = DEFAULT_MAX_RESULT_SIZE_CHARS,
) -> str:
    """
    If output exceeds max_chars, persist to disk and return preview.

    Returns: original output if small enough, or persisted preview message.
    """
    if len(output) <= max_chars:
        return output

    if not session_dir:
        return output[:max_chars] + "\n... [output truncated]"

    results_dir = session_dir / "tool-results"
    results_dir.mkdir(parents=True, exist_ok=True)

    result_path = results_dir / f"{tool_use_id}.txt"
    try:
        result_path.write_text(output, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to persist tool result: {e}")
        return output[:max_chars] + "\n... [output truncated]"

    preview = output[:PREVIEW_SIZE_BYTES]
    last_nl = preview.rfind("\n")
    if last_nl > 0:
        preview = preview[:last_nl]

    total_bytes = len(output.encode("utf-8", errors="replace"))

    return (
        f"<persisted-output>\n"
        f"Output too large ({total_bytes:,} bytes). Full output saved to: {result_path}\n"
        f"\n"
        f"Preview (first {PREVIEW_SIZE_BYTES} bytes):\n"
        f"{preview}\n"
        f"...\n"
        f"</persisted-output>"
    )
