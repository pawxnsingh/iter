"""
Pill-shaped status badge — two half-blocks framing styled text.

Ported from toad/pill.py. The ▌ and ▐ characters in reverse video create
the rounded-cap effect you see in GitHub labels.
"""

from __future__ import annotations

from textual.content import Content


def pill(
    text: Content | str,
    background: str = "$primary",
    foreground: str = "$text-primary",
) -> Content:
    """Wrap text as a reverse-video pill with half-block caps."""
    content = Content(text) if isinstance(text, str) else text
    main_style = f"{foreground} on {background}"
    end_style = f"{background} on transparent r"
    return Content.assemble(
        ("▌", end_style),
        content.stylize(main_style),
        ("▐", end_style),
    )
