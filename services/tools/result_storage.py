"""
Tool result storage — persist large results to disk.

Source: Claude Code src/utils/toolResultStorage.ts

When a tool result exceeds maxResultSizeChars, it's persisted to disk
and replaced with a preview + file path. This prevents large results
from consuming the entire context window.

Constants (from Claude Code):
  DEFAULT_MAX_RESULT_SIZE_CHARS = 100,000  (CC's common per-tool value; the
      real cap is per-tool via Tool.max_result_size_chars — Grep 20k, Bash 30k.
      This fallback only applies if a caller omits max_chars.)
  MAX_TOOL_RESULT_BYTES = 400,000
  PREVIEW_SIZE_BYTES = 2,000
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Aligned with Tool.max_result_size_chars default (100k) — the previous 50k
# was never actually used (truncate_tool_result always passes the tool's value)
# and only created a confusing mismatch.
DEFAULT_MAX_RESULT_SIZE_CHARS = 100_000
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
    Claude Code: maybePersistLargeToolResult() in toolResultStorage.ts

    Returns: original output if small enough, or persisted preview message.
    """
    if len(output) <= max_chars:
        return output

    if not session_dir:
        # No session dir — just truncate inline
        return output[:max_chars] + "\n... [output truncated]"

    # Persist to disk
    results_dir = session_dir / "tool-results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Write full output
    result_path = results_dir / f"{tool_use_id}.txt"
    try:
        result_path.write_text(output, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to persist tool result: {e}")
        return output[:max_chars] + "\n... [output truncated]"

    # Build preview (first PREVIEW_SIZE_BYTES bytes)
    preview = output[:PREVIEW_SIZE_BYTES]
    # Cut at last newline for clean preview
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
