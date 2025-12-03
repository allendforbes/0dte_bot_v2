import asyncio
import time
import random

class MockMassiveWSAdapter:
    """
    Full-feature mock Massive WS adapter
    Produces NBBO ticks with Greeks + IV change.
    Compatible with:
        • MassiveContractEngine
        • ChainAggregator
        • Orchestrator
        • EliteLatencyPrecheck v4
    """

    def __init__(self):
        self._nbbo_handlers = []
        self._quote_handlers = []
        self._reconnect_handlers = []

        self.contracts = []
        self._task = None
        self._running = False

        # Last IV per contract (to compute iv_change)
        self._last_iv = {}

    # ---------------------------------------------------------
    # CALLBACK REGISTRATION
    # ---------------------------------------------------------
    def on_nbbo(self, cb):
        self._nbbo_handlers.append(cb)

    def on_quote(self, cb):
        self._quote_handlers.append(cb)

    def on_reconnect(self, cb):
        self._reconnect_handlers.append(cb)

    # ---------------------------------------------------------
    async def connect(self):
        """Instant OK."""
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())

    async def close(self):
        self._running = False
        if self._task:
            self._task.cancel()

    # ---------------------------------------------------------
    async def subscribe_contracts(self, occ_list):
        """Record subscriptions & allow immediate warm-up."""
        self.contracts.extend(occ_list)
        await asyncio.sleep(0.01)

    # ---------------------------------------------------------
    async def _tick_loop(self):
        """Generate NBBO ticks with Greeks + IV dynamics."""
        while self._running:

            now = time.time()

            for occ in self.contracts:

                # Basic OCC decode
                sym = occ[2:5]           # SPY
                right = occ[11]          # C or P
                strike = int(occ[12:]) / 1000.0

                # ---------------------------
                # Bid/ask simulation
                # ---------------------------
                bid = round(random.uniform(0.50, 3.00), 2)
                ask = bid + round(random.uniform(0.02, 0.12), 2)

                # ---------------------------
                # Greeks / IV simulation
                # ---------------------------
                iv = round(random.uniform(0.15, 0.35), 4)

                last_iv = self._last_iv.get(occ, iv)
                iv_change = round(iv - last_iv, 4)
                self._last_iv[occ] = iv

                delta = round(random.uniform(0.25, 0.65), 4)
                gamma = round(random.uniform(0.01, 0.05), 4)
                theta = round(random.uniform(-0.08, -0.01), 4)
                vega  = round(random.uniform(0.05, 0.15), 4)

                # ---------------------------
                # Build Massive-like event
                # ---------------------------
                event = {
                    "ev": "NO",
                    "sym": occ,
                    "symbol": sym,
                    "contract": occ,
                    "right": right,
                    "strike": strike,

                    # Quotes
                    "bp": bid,
                    "ap": ask,
                    "bs": random.randint(5, 25),
                    "as": random.randint(5, 25),

                    # Greeks / IV
                    "iv": iv,
                    "iv_change": iv_change,
                    "delta": delta,
                    "gamma": gamma,
                    "theta": theta,
                    "vega": vega,

                    # Volume
                    "vol": random.randint(200, 8000),
                    "oi": random.randint(5000, 60000),

                    # Orchestrator/LPC timing
                    "_recv_ts": now,
                    "_chain_age": 0.01,   # Always fresh
                }

                # Route to NBBO handlers
                for cb in self._nbbo_handlers:
                    asyncio.create_task(cb(event))

            await asyncio.sleep(0.05)
