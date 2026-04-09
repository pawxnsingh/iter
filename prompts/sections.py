"""
System prompt sections — cacheable static + dynamic per-turn.

Source: Claude Code src/constants/systemPromptSections.ts

Static sections are identical across turns (cacheable).
Dynamic sections change per-turn but are memoized within a turn.
This avoids regenerating expensive sections every API call.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Cache for static sections (cleared on compact)
_section_cache: dict[str, str | None] = {}


def system_prompt_section(name: str, compute_fn: Callable[[], str | None]) -> str | None:
    """
    Memoized system prompt section.
    Claude Code: systemPromptSection() in systemPromptSections.ts

    Caches the result so repeated calls within a turn don't recompute.
    Reset via clear_system_prompt_sections() after compaction.
    """
    if name in _section_cache:
        return _section_cache[name]

    try:
        result = compute_fn()
        _section_cache[name] = result
        return result
    except Exception as e:
        logger.warning(f"Error computing prompt section '{name}': {e}")
        _section_cache[name] = None
        return None


def clear_system_prompt_sections() -> None:
    """
    Clear cached sections — called after compaction or config change.
    Claude Code: clearSystemPromptSections() in postCompactCleanup.ts
    """
    _section_cache.clear()


def get_cached_section(name: str) -> str | None:
    """Get a cached section without recomputing."""
    return _section_cache.get(name)
