import asyncio
import random
import time
from typing import Dict, Any


class SyntheticNBBOFeed:
    """
    PRO-Format Synthetic NBBO Generator (A2-M Compatible)

    Generates PRO-format NBBO frames matching WSAdapterPRO:

        {
            "sym": "O:SPY20250117C00400000",
            "b": 1.23,
            "a": 1.25,
            "ts": 1737052200.1234
        }

    These are sent as *batches* via:

        await mux.push_option_batch(batch)

    SyntheticMux v3.2 then:
        ✓ hydrates ChainFreshnessV2
        ✓ expands PRO rows into aggregator-friendly format
        ✓ forwards per-option NBBO rows to orchestrator
    """

    def __init__(self, mux, symbol: str, expiry: str, underlying, strike_inc=1):
        self.mux = mux               # SyntheticMux v3.2
        self.symbol = symbol
        self.expiry = expiry
        self.underlying = underlying
        self.strike_inc = strike_inc

        self._running = False
        self._last_under_price = underlying.price

        # Synthetic microstructure state
        self.iv_level = 0.22
        self.flow_bias = 0  # +1 = call heavy, -1 = put heavy

    # -----------------------------------------------------------
    def stop(self):
        self._running = False

    # -----------------------------------------------------------
    async def start(self):
        """
        Main synthetic NBBO loop — emits PRO-format NBBO batches
        every ~80ms, matching WSAdapterPRO timing.
        """
        self._running = True

        while self._running:

            u = self.underlying.price
            dt = max(0.01, abs(u - self._last_under_price))
            self._last_under_price = u

            # ---------------------------------------------------
            # FLOW MODEL — creates call/put imbalance
            # ---------------------------------------------------
            if dt > 0.20:          # breakout
                self.flow_bias += random.uniform(0.3, 0.8)
            elif dt < 0.05:        # chop → mean revert
                self.flow_bias *= 0.90
            else:                  # weak trend
                self.flow_bias += random.uniform(-0.1, 0.1)

            self.flow_bias = max(-3, min(3, self.flow_bias))

            # ---------------------------------------------------
            # IV MODEL — expands on volatility
            # ---------------------------------------------------
            if dt > 0.15:
                self.iv_level += random.uniform(0.01, 0.03)
            else:
                self.iv_level -= random.uniform(0.005, 0.015)

            self.iv_level = max(0.12, min(0.45, self.iv_level))

            # ---------------------------------------------------
            # BUILD BATCH — ATM ±2 strikes each cycle
            # ---------------------------------------------------
            atm = round(u)
            strikes = [
                atm - 2 * self.strike_inc,
                atm - 1 * self.strike_inc,
                atm,
                atm + 1 * self.strike_inc,
                atm + 2 * self.strike_inc,
            ]

            batch = []

            for strike in strikes:
                for right in ["C", "P"]:

                    mid = self._calc_midprice(u, strike, right)
                    bid, ask = self._apply_spread(mid, dt)

                    # PRO-style NBBO frame (A2-M compliant)
                    mid = (bid + ask) / 2

                    batch.append({
                        "sym": self._occ(strike, right),  # OCC code (O:SPY20250117C00400000)
                        "b": round(bid, 2),
                        "a": round(ask, 2),
                        "iv": round(self.iv_level + random.uniform(-0.02, 0.02), 4),
                        "vol": random.randint(5, 250),         # realistic intraday volume
                        "oi": random.randint(500, 5000),       # typical SPY OI levels
                        "premium": round(mid, 3),              # orchestrator uses premium
                        "ts": time.time(),
                    })

            # ---------------------------------------------------
            # SEND BATCH → SyntheticMux → Orchestrator
            # ---------------------------------------------------
            await self.mux.push_option_batch(batch)

            await asyncio.sleep(0.08)

    # -----------------------------------------------------------
    def _calc_midprice(self, u, strike, right):
        intrinsic = max(0, u - strike) if right == "C" else max(0, strike - u)
        extrinsic = self.iv_level * (1 + random.uniform(-0.15, 0.15))
        return max(0.05, intrinsic + extrinsic)

    # -----------------------------------------------------------
    def _apply_spread(self, mid, dt):
        """
        Spread tightens during trend, widens during chop — like real markets.
        """
        if dt > 0.15:
            sp = random.uniform(0.01, 0.04)
        elif dt < 0.05:
            sp = random.uniform(0.05, 0.12)
        else:
            sp = random.uniform(0.02, 0.08)

        return mid - sp / 2, mid + sp / 2

    # -----------------------------------------------------------
    def _occ(self, strike, right):
        s = int(strike * 1000)
        # PRO format uses "O:" prefix just like WSAdapterPRO
        return f"O:{self.symbol}{self.expiry}{right}{s:08d}"
