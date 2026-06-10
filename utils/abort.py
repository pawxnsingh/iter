"""
AbortController for cancellation propagation.

Claude Code uses AbortController + signal cascading so user Ctrl+C
propagates through the tool execution chain. We replicate this with
asyncio.Event.
"""

from __future__ import annotations

import asyncio


class AbortController:
    """
    Cancellation controller. 
    """

    def __init__(self):
        self._event = asyncio.Event()
        self._reason: str | None = None
        self._children: list[AbortController] = []

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def abort(self, reason: str = "aborted") -> None:
        """Signal abort. Cascades to all child controllers."""
        if self._event.is_set():
            return
        self._reason = reason
        self._event.set()
        for child in self._children:
            child.abort(reason)

    def create_child(self) -> AbortController:
        """
        Create a child controller. If parent aborts, child aborts too.
        Mirrors Claude Code's createChildAbortController().
        """
        child = AbortController()
        self._children.append(child)
        return child

    async def wait_for_abort(self) -> str:
        """Wait until aborted. Returns the reason."""
        await self._event.wait()
        return self._reason or "aborted"

    def check(self) -> None:
        """Raise if aborted. Use in tool execution loops."""
        if self.aborted:
            raise AbortError(self._reason or "aborted")


class AbortError(Exception):
    """Raised when an operation is aborted via AbortController."""
    def __init__(self, reason: str = "aborted"):
        self.reason = reason
        super().__init__(f"Operation aborted: {reason}")
