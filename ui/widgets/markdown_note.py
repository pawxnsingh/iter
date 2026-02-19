"""
Markdown-rendered info block.

Port of toad/widgets/markdown_note.py.
"""

from __future__ import annotations

from typing import Iterable

from textual.widgets import Markdown

from ui.protocols import MenuItem


class MarkdownNote(Markdown):
    DEFAULT_CLASSES = "block"

    def __init__(self, markdown: str = "", **kwargs):
        super().__init__(markdown, **kwargs)
        # Textual's Markdown.source only populates after mount/update — keep
        # our own copy so get_block_content works pre-mount too.
        self._markdown_source = markdown

    async def update(self, markdown: str):
        self._markdown_source = markdown
        return await super().update(markdown)

    def get_block_menu(self) -> Iterable[MenuItem]:
        return
        yield  # pragma: no cover — generator shape

    def get_block_content(self, destination: str) -> str | None:
        if destination == "clipboard":
            return str(self.render())
        return self._markdown_source or self.source
