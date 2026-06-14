"""
Microcompact: intentionally a no-op.

An earlier implementation cleared every pre-boundary tool result on each API
call, which aggressively destroyed context the model still needed. The only
legitimate clearing variants are narrow and default-off (a time-based path
gated on a >60-minute idle gap, and a cache-editing path that does not apply
here), so this layer does nothing and autocompact (LLM summarisation) handles
context pressure instead.

A time-based variant is intentionally not implemented: it would be disabled by
default, and MessageItem carries no per-message timestamp to gate it on, so it
would be dead-by-default code.
"""

from __future__ import annotations


def micro_compact(messages: list) -> list:
    """No-op: returns the message list unchanged; autocompact handles
    context pressure."""
    return messages
