"""
Adapters package (IBKR, REST, WebSocket, etc.)

Note:
    Originally contained legacy adapters.
    Now extended to include IBUnderlyingAdapter (IBKR real-time underlying feed).
"""

from .ib_underlying_adapter import IBUnderlyingAdapter

__all__ = ["IBUnderlyingAdapter"]
