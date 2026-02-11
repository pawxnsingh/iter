"""
Format a path using `~/` shorthand when possible.

Port of toad/format_path.py — identical semantics.
"""

from __future__ import annotations

from pathlib import Path


def format_path(path: Path | str, directory: bool = False) -> str:
    """Format a path, using ~/ syntax where appropriate.

    Args:
        path: Input path.
        directory: Append separator to mark as directory.
    """
    if isinstance(path, str):
        path = Path(path)
    path = path.expanduser().resolve()
    try:
        relative = path.relative_to(Path.home())
        formatted = f"~/{relative}"
    except ValueError:
        formatted = str(path)
    if directory and not formatted.endswith("/"):
        formatted += "/"
    return formatted
