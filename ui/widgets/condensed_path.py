"""
Single-line path display with middle-ellipsis truncation.

Port of toad/widgets/condensed_path.py — the `radiate_range` technique
preserves the most meaningful parts (start + end) of a path when it
can't fit, collapsing the middle to `…`.
"""

from __future__ import annotations

import os.path
from functools import lru_cache
from typing import Iterable

from rich.cells import cell_len
from textual.content import Content
from textual.geometry import Size
from textual.reactive import reactive
from textual.widget import Widget


def radiate_range(total: int) -> Iterable[tuple[int, int]]:
    """Generate (left, right) index pairs that grow outward from the center.

    Used to decide which path components to drop first when truncating.
    """
    if not total:
        return
    left = right = total // 2
    yield (left, right)
    while left >= 0 or right < total:
        left -= 1
        if left >= 0:
            yield (left + 1, right)
        right += 1
        if right <= total:
            yield (left + 1, right)


@lru_cache(maxsize=32)
def condense_path(path: str, width: int, *, prefix: str = "") -> str:
    """Condense `path` to fit `width`, replacing middle components with `…`."""
    if cell_len(path) <= width:
        return path
    components = path.split("/")
    condensed = components
    trailing_slash = path.endswith("/")
    candidate = prefix + "/".join(condensed)
    if trailing_slash and candidate and not candidate.endswith("/"):
        candidate += "/"

    for left, right in radiate_range(len(components)):
        if cell_len(candidate) < width:
            return candidate
        condensed = [*components[:left], "…", *components[right:]]
        candidate = prefix + "/".join(condensed)
        if trailing_slash and candidate and not candidate.endswith("/"):
            candidate += "/"

    return candidate


class CondensedPath(Widget):
    DEFAULT_CSS = """
    CondensedPath { height: 1; }
    """

    path: reactive[str] = reactive("")
    display_path: reactive[str] = reactive("")

    def __init__(
        self,
        path: str = "",
        *,
        directory: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.set_reactive(CondensedPath.path, path)
        self._directory = directory

    def on_resize(self) -> None:
        self.watch_path(self.path)

    def watch_path(self, path: str) -> None:
        if not path or not self.size.width:
            return
        path = os.path.abspath(os.path.expanduser(path))
        if not self.tooltip:
            self.tooltip = str(path)
        user_root = os.path.abspath(os.path.expanduser("~/"))
        if not user_root.endswith("/"):
            user_root += "/"
        if path.startswith(user_root):
            path = "~/" + path[len(user_root):]
        if self._directory and not path.endswith("/"):
            path += "/"
        self.display_path = path

    def render(self) -> Content:
        self.watch_path(self.path)
        return Content(condense_path(self.display_path, self.size.width))

    def get_content_width(self, container: Size, viewport: Size) -> int:
        if self.display_path:
            return Content(self.display_path).cell_length
        return container.width
