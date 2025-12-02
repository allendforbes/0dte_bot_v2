"""
fake_engine.py — lightweight engine + fake underlying pipe for simulation
"""

import asyncio
import time
from typing import Dict, Any, Callable, List


# ================================================================
# Fake Underlying Pipe
# ================================================================
class FakeUnderlyingPipe:
    """
    Minimal underlying feed that matches IBUnderlyingAdapter public API:
        • on_underlying(callback)
        • loop
    """

    def __init__(self):
        self._handlers: List[Callable] = []
        self.loop = asyncio.get_event_loop()

    def on_underlying(self, cb: Callable):
        self._handlers.append(cb)

    async def emit(self, event: Dict[str, Any]):
        """Simulate receiving a tick."""
        for cb in self._handlers:
            self.loop.create_task(cb(event))


# ================================================================
# Fake Execution Engine
# ================================================================
class FakeExecutionEngine:
    """
    Barebones execution engine used for replay simulations.
    No orders are sent; only state is updated.
    """

    class _AcctState:
        def __init__(self, net_liq):
            self.net_liq = net_liq
            self._fresh = True

        def is_fresh(self):
            return self._fresh

    def __init__(self, net_liq: float = 25000):
        self.account_state = self._AcctState(net_liq)
        self.last_price = {}  # symbol → underlying price

    async def start(self):
        """In simulation mode nothing is started."""
        return

    async def send_market(self, **kwargs):
        """
        Simulated market order — returns shadow metadata.
        """
        return {
            "status": "shadow",
            "echo": kwargs,
            "ts": time.time(),
        }

    # Convenience helper
    @staticmethod
    def make_fake_underlying_pipe():
        return FakeUnderlyingPipe()
