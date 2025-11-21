# bot_0dte/strategy/strike_selector.py

"""
Hybrid WS-Native StrikeSelector (Convexity + Ultra-Low-Latency)
---------------------------------------------------------------
Consumes ChainAggregator rows produced from Massive NBBO + Quotes.

Selection Logic:
    1. Filter CALL/PUT by bias
    2. Reject rows with missing bid/ask
    3. Determine ATM from underlying price
    4. Build ATM ±1 ±2 cluster
    5. Calculate mid and apply premium <= $1 ceiling
    6. Select contract closest to $1
    7. Tiebreak by strike proximity to ATM (closer is better)

Massive chain row format:
{
    "symbol": "SPY",
    "strike": 450,
    "right": "C",
    "premium": 0.95,
    "bid": 0.90,
    "ask": 1.00,
    "contract": "O:SPY241122C00450000"
}
"""

import numpy as np
import pandas as pd


class StrikeSelector:
    PREMIUM_CEILING = 1.00
    MAX_ATM_DISTANCE = 2  # ATM ±1 ±2

    def __init__(self, chain_bridge=None, engine=None):
        """
        IBKR is no longer used.
        engine is passed so we can read engine.last_price[symbol].
        """
        self.engine = engine

    # ------------------------------------------------------------------
    def _cluster_strikes(self, underlying_price, strikes):
        """Return ATM ±1 ±2 cluster."""
        if underlying_price is None or not strikes:
            return []

        atm = min(strikes, key=lambda k: abs(k - underlying_price))
        cluster = [atm]

        for k in range(1, self.MAX_ATM_DISTANCE + 1):
            if (atm - k) in strikes:
                cluster.append(atm - k)
            if (atm + k) in strikes:
                cluster.append(atm + k)

        return cluster

    # ------------------------------------------------------------------
    async def select_from_chain(self, chain_rows, bias):
        """
        Selects one optimal strike from the WS-native chain rows.
        """
        if not chain_rows:
            return None

        # ---------------------------------------------------------------
        # 1 — FILTER BY SIDE
        # ---------------------------------------------------------------
        side = "C" if bias.upper() == "CALL" else "P"
        rows = [r for r in chain_rows if r.get("right") == side]
        if not rows:
            return None

        # ---------------------------------------------------------------
        # 2 — PRICE SANITY
        # Must have bid/ask, discard missing quotes
        # ---------------------------------------------------------------
        priced = [r for r in rows if r.get("bid") and r.get("ask")]
        if not priced:
            return None

        # ---------------------------------------------------------------
        # 3 — UNDERLYING PRICE
        # We use this to determine ATM
        # ---------------------------------------------------------------
        symbol = priced[0].get("symbol")
        underlying = self.engine.last_price.get(symbol)
        if underlying is None:
            return None

        # ---------------------------------------------------------------
        # 4 — ATM ±1 ±2 STRIKE CLUSTER
        # ---------------------------------------------------------------
        strikes = sorted({float(r["strike"]) for r in priced if r.get("strike")})
        cluster = self._cluster_strikes(underlying, strikes)
        if not cluster:
            return None

        clustered_rows = [r for r in priced if float(r["strike"]) in cluster]
        if not clustered_rows:
            return None

        # ---------------------------------------------------------------
        # 5 — MIDPRICE & PREMIUM CEILING
        # ---------------------------------------------------------------
        enriched = []
        for r in clustered_rows:
            bid = float(r.get("bid", 0.0))
            ask = float(r.get("ask", 0.0))
            mid = (bid + ask) / 2

            if mid <= 0:
                continue
            if mid > self.PREMIUM_CEILING:
                continue

            enriched.append(
                {
                    **r,
                    "mid": mid,
                    "dist": abs(mid - self.PREMIUM_CEILING),
                    "atm_dist": abs(float(r["strike"]) - underlying),
                }
            )

        if not enriched:
            return None

        # ---------------------------------------------------------------
        # 6 — SORT BY:
        #      1) closeness to $1 premium
        #      2) closeness to ATM
        # ---------------------------------------------------------------
        enriched.sort(key=lambda r: (r["dist"], r["atm_dist"]))
        best = enriched[0]

        return {
            "symbol": symbol,
            "strike": float(best["strike"]),
            "right": side,
            "premium": round(best["mid"], 2),
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "contract": best["contract"],
        }
