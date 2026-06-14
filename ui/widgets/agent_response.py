"""
Streaming Markdown widget for assistant responses.

Pattern (from toad/widgets/agent_response.py):
  - Subclass Textual's Markdown widget.
  - Hold a MarkdownStream obtained via `self.get_stream(self)`.
  - Each incoming text chunk is appended via `await stream.write(chunk)`,
    which incrementally updates the rendered widget tree — no flicker,
    no re-parsing of previously-seen markdown.

On older Textual versions MarkdownStream may live on a different import
path — we import defensively.
"""

from __future__ import annotations

from textual.reactive import var
from textual.widgets import Markdown

try:
    from textual.widgets._markdown import MarkdownStream  # noqa: F401
except ImportError:  # pragma: no cover — pre-MarkdownStream Textual
    MarkdownStream = None  # type: ignore[assignment]


class AgentResponse(Markdown):
    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    AgentResponse {
        min-height: 1;
        padding: 0 0 0 0;
        margin: 1 1 0 0;
        overflow-x: auto;
        scrollbar-size-horizontal: 0;
        & > MarkdownBlock:last-child { margin-bottom: 0; }
    }
    """

    block_cursor_offset = var(-1)

    def __init__(self, initial: str = "") -> None:
        super().__init__(initial)
        self._stream = None

    async def append_fragment(self, fragment: str) -> None:
        """Append a streaming text chunk."""
        if not fragment:
            return
        if self._stream is None:
            self._stream = self.get_stream(self)
        self.loading = False
        await self._stream.write(fragment)
