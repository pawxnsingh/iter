"""
Protocols for block-level widget behavior.

Port of toad/protocol.py.

Any block widget inside Conversation may implement one or more of these
protocols. Conversation inspects children polymorphically — it doesn't
need to import concrete types to dispatch block-cursor navigation,
context menus, or expansion.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from textual.widget import Widget


# Placeholder for MenuItem — toad uses its own menus.py; we keep the
# shape simple so we can grow the context-menu system later.
MenuItem = object


@runtime_checkable
class BlockProtocol(Protocol):
    def block_cursor_up(self) -> Widget | None: ...
    def block_cursor_down(self) -> Widget | None: ...
    def get_cursor_block(self) -> Widget | None: ...
    def block_cursor_clear(self) -> None: ...
    def block_select(self, widget: Widget) -> None: ...


@runtime_checkable
class MenuProtocol(Protocol):
    def get_block_menu(self) -> Iterable[MenuItem]: ...
    def get_block_content(self, destination: str) -> str | None: ...


@runtime_checkable
class ExpandProtocol(Protocol):
    def can_expand(self) -> bool: ...
    def expand_block(self) -> None: ...
    def collapse_block(self) -> None: ...
    def is_block_expanded(self) -> bool: ...
