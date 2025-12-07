import asyncio
import math
import random
import time
from typing import Dict, Any, Optional


class SyntheticUnderlyingFeed:
    """
    Synthetic Underlying Simulator for A2-M validation.

    Responsibilities:
        • Produce underlying price ticks for each symbol
        • Model:
            – drift (trend up/down)
            – volatility (σ)
            – micro-chop / noise
            – volume (for VWAP weighting)
        • Maintain realistic tick cadence (20–80ms)
        • Inject occasional latency jitter
        • Push events through SyntheticMux

    The orchestrator sees this as if from IBKR.
    """

    def __init__(
        self,
        mux,
        symbol: str,
        start_price: float,
        drift: float = 0.03,
        volatility: float = 0.8,
        tick_interval_ms: tuple = (20, 80),
    ):
        self.mux = mux
        self.symbol = symbol
        self.price = start_price

        # model parameters
        self.drift = drift               # directional drift (points/sec)
        self.sigma = volatility          # intraday volatility
        self.tick_lo, self.tick_hi = tick_interval_ms

        self._running = False

    async def start(self):
        """Begin streaming synthetic underlying ticks."""
        self._running = True
        last_ts = time.time()

        while self._running:
            now = time.time()
            dt = now - last_ts
            last_ts = now

            # drift + noise
            noise = random.gauss(0, self.sigma) * math.sqrt(dt)
            self.price += self.drift * dt + noise

            # synthetic bid/ask microstructure
            spread = max(0.01, random.gauss(0.02, 0.005))
            bid = self.price - spread / 2
            ask = self.price + spread / 2

            # volume estimate for VWAP weighting
            vol = max(1, int(abs(random.gauss(20, 5))))

            event = {
                "symbol": self.symbol,
                "price": round(self.price, 3),
                "bid": round(bid, 3),
                "ask": round(ask, 3),
                "volume": vol,
                "_ts": time.time(),
            }

            await self.mux.push_underlying(event)

            # random latency jitter (simulate IBKR)
            ms = random.randint(self.tick_lo, self.tick_hi)
            await asyncio.sleep(ms / 1000.0)

    def stop(self):
        self._running = False
