"""
Massive.com WebSocket Provider
Minimal exports to avoid circular imports.
"""

from .massive_options_ws_adapter import MassiveOptionsWSAdapter

__all__ = [
    "MassiveOptionsWSAdapter",
]
