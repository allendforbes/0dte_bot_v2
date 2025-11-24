"""
bot_0dte - 0DTE Options Trading Bot
WebSocket-native architecture with Massive.com feeds.
"""

__version__ = "2.0.0-ws-native"
__author__  = "Allen"

from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol
from bot_0dte.sizing import size_from_equity

__all__ = [
    "get_universe_for_today",
    "get_expiry_for_symbol",
    "size_from_equity",
]
