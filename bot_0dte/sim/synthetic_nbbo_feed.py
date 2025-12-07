import asyncio
import random
import time
from typing import Dict, Any


class SyntheticNBBOFeed:
    """
    Realistic A2-M Synthetic NBBO Generator

    Models:
        • Directional call/put volume imbalance
        • Skew shifts during momentum reversals
        • IV expansion during volatility spikes
        • IV crush during stagnation
        • Gamma increases near ATM
        • Delta moves with underlying
        • Spread tightens in trend, widens in chop
    """

    def __init__(self, mux, symbol: str, expiry: str, underlying, strike_inc=1):
        self.mux = mux
        self.symbol = symbol
        self.expiry = expiry
        self.underlying = underlying
        self.strike_inc = strike_inc

        self._running = False
        self._last_under_price = underlying.price

        # State for microstructure
        self.iv_level = 0.22
        self.flow_bias = 0  # +1 = call heavy, -1 = put heavy

    # -----------------------------------------------------------
    def stop(self):
        self._running = False

    # -----------------------------------------------------------
    async def start(self):
        self._running = True
        while self._running:

            u = self.underlying.price
            dt = max(0.01, abs(u - self._last_under_price))
            self._last_under_price = u

            # ---------------------------------------------------
            # FLOW MODEL (key for A2-M)
            # ---------------------------------------------------
            if dt > 0.20:        # breakout conditions
                self.flow_bias += random.uniform(0.3, 0.8)
            elif dt < 0.05:      # chop → flatten
                self.flow_bias *= 0.90
            else:                # weak trend
                self.flow_bias += random.uniform(-0.1, 0.1)

            self.flow_bias = max(-3, min(3, self.flow_bias))

            call_flow = max(1, int(100 + 80 * max(0, self.flow_bias)))
            put_flow = max(1, int(100 + 80 * max(0, -self.flow_bias)))

            upvol_pct = call_flow / (call_flow + put_flow) * 100

            # ---------------------------------------------------
            # IV MODEL
            # ---------------------------------------------------
            if dt > 0.15:  # volatility / breakout
                self.iv_level += random.uniform(0.01, 0.03)
            else:
                self.iv_level -= random.uniform(0.005, 0.015)

            self.iv_level = max(0.12, min(0.45, self.iv_level))

            # ---------------------------------------------------
            # BUILD OPTION CHAIN (ATM ±2)
            # ---------------------------------------------------
            atm = round(u)
            strikes = [
                atm - 2 * self.strike_inc,
                atm - 1 * self.strike_inc,
                atm,
                atm + 1 * self.strike_inc,
                atm + 2 * self.strike_inc,
            ]

            for strike in strikes:
                for right in ["C", "P"]:
                    mid = self._calc_midprice(u, strike, right)
                    bid, ask = self._apply_spread(mid, dt)

                    delta = self._calc_delta(u, strike, right)
                    gamma = self._calc_gamma(u, strike)

                    volume = call_flow if right == "C" else put_flow

                    event = {
                        "symbol": self.symbol,
                        "contract": self._occ(strike, right),
                        "right": right,
                        "strike": float(strike),
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "premium": round(mid, 2),
                        "delta": round(delta, 3),
                        "gamma": round(gamma, 4),
                        "iv": round(self.iv_level, 4),
                        "volume": volume,
                        "_recv_ts": time.time(),
                        "_chain_age": 0.01,
                    }

                    await self.mux.push_option(event)

            await asyncio.sleep(0.08)

    # -----------------------------------------------------------
    def _calc_midprice(self, u, strike, right):
        intrinsic = max(0, u - strike) if right == "C" else max(0, strike - u)
        extrinsic = self.iv_level * (1 + random.uniform(-0.15, 0.15))
        return max(0.05, intrinsic + extrinsic)

    # -----------------------------------------------------------
    def _apply_spread(self, mid, dt):
        """
        Spread narrows on trend, widens on chop — crucial for A2-M spreads.
        """
        if dt > 0.15:  # trend breakout
            sp = random.uniform(0.01, 0.04)
        elif dt < 0.05:  # chop
            sp = random.uniform(0.05, 0.12)
        else:
            sp = random.uniform(0.02, 0.08)

        bid = mid - sp / 2
        ask = mid + sp / 2
        return bid, ask

    # -----------------------------------------------------------
    def _calc_delta(self, u, strike, right):
        dist = abs(u - strike)
        raw = max(0.05, 0.45 - 0.12 * dist)
        return raw if right == "C" else -raw

    # -----------------------------------------------------------
    def _calc_gamma(self, u, strike):
        dist = abs(u - strike)
        return max(0.005, 0.03 - 0.01 * dist)

    # -----------------------------------------------------------
    def _occ(self, strike, right):
        s = int(strike * 1000)
        return f"O:{self.symbol}{self.expiry}{right}{s:08d}"
