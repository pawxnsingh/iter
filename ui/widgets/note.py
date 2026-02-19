"""
Plain info block, implements MenuProtocol.

Port of toad/widgets/note.py. Semantic variants (`-error`, `-warning`,
`-stop-reason`) styled in screens/main.tcss.
"""

from __future__ import annotations

from typing import Iterable

from textual.widgets import Static

from ui.protocols import MenuItem


class Note(Static):
    DEFAULT_CLASSES = "block"

    def get_block_menu(self) -> Iterable[MenuItem]:
        return
        yield  # pragma: no cover — generator shape

    def get_block_content(self, destination: str) -> str | None:
        return str(self.render())
