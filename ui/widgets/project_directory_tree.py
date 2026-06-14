"""
Directory tree scoped to the project — hides noise like `.git`, `__pycache__`,
`.venv`, etc.

Thin subclass of Textual's `DirectoryTree` with path filtering.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from textual.reactive import reactive
from textual.widgets import DirectoryTree


_HIDDEN_PATTERNS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".DS_Store",
    ".idea",
    ".vscode",
}


class ProjectDirectoryTree(DirectoryTree):
    """Filtered DirectoryTree. Use `path_filter` to add extra excludes."""

    path: reactive[Path] = reactive(Path("./"))
    extra_excludes: reactive[frozenset[str]] = reactive(frozenset())

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        excludes = _HIDDEN_PATTERNS | set(self.extra_excludes)
        return [p for p in paths if p.name not in excludes]

    def watch_path(self, path: Path) -> None:
        try:
            if str(self.path) != str(path):
                return
            self.path_ = str(path)
            self.reload()
        except Exception:
            pass
