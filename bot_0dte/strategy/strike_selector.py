"""
StrikeSelector v3.1 — Dual-Ceiling + Delta-Targeted ATM± Selection
-----------------------------------------------------------------
SPY/QQQ:
    • Premium ceiling: $1.00 (always)

MATMAN (AAPL, AMZN, META, MSFT, NVDA, TSLA):
    • Mon–Thu ceiling: $1.50
    • Friday ceiling: $1.25

Selection Priorities:
    1. Premium <= dynamic ceiling
    2. ATM proximity (clustered ±2 strikes)
    3. High gamma (convexity)
    4. Delta near ±0.30
    5. Freshest NBBO timestamp
"""

import datetime as dt


class StrikeSelector:
    MAX_ATM_DISTANCE = 2

    CORE = {"SPY", "QQQ"}
    MATMAN = {"AAPL", "AMZN", "META", "MSFT", "NVDA", "TSLA"}

    CORE_CEILING = 1.00
    MATMAN_CEILING_MON_THU = 1.50
    MATMAN_CEILING_FRI = 1.25

    TARGET_DELTA_CALL = 0.30
    TARGET_DELTA_PUT = -0.30

    def __init__(self):
        pass

    # ---------------------------------------------------
    def _premium_ceiling_for(self, symbol: str) -> float:
        """Return the correct premium ceiling based on symbol + weekday."""
        wd = dt.datetime.now().weekday()  # Mon=0 ... Fri=4

        if symbol in self.CORE:
            return self.CORE_CEILING

        if symbol in self.MATMAN:
            if wd == 4:  # Friday
                return self.MATMAN_CEILING_FRI
            return self.MATMAN_CEILING_MON_THU

        return 1.00  # fallback

    # ---------------------------------------------------
    def _cluster_strikes(self, underlying, strikes):
        if underlying is None or not strikes:
            return []

        atm = min(strikes, key=lambda s: abs(s - underlying))
        cluster = {atm}

        for k in range(1, self.MAX_ATM_DISTANCE + 1):
            if atm - k in strikes:
                cluster.add(atm - k)
            if atm + k in strikes:
                cluster.add(atm + k)

        return list(cluster)

    # ---------------------------------------------------
    async def select_from_chain(self, chain_rows, bias, underlying_price):
        if not chain_rows or underlying_price is None:
            return None

        side = "C" if bias.upper() == "CALL" else "P"

        rows = [r for r in chain_rows if r["right"] == side]
        if not rows:
            return None

        symbol = rows[0]["symbol"]
        ceiling = self._premium_ceiling_for(symbol)

        strikes = sorted({float(r["strike"]) for r in rows if r["strike"] is not None})
        cluster = self._cluster_strikes(underlying_price, strikes)

        rows = [r for r in rows if float(r["strike"]) in cluster]
        if not rows:
            return None

        enriched = []
        target_delta = self.TARGET_DELTA_CALL if side == "C" else self.TARGET_DELTA_PUT

        for r in rows:
            bid, ask = r["bid"], r["ask"]
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            if mid <= 0 or mid > ceiling:
                continue

            gamma = r.get("gamma") or 0.0
            delta = r.get("delta") or 0.0

            enriched.append(
                {
                    **r,
                    "mid": mid,
                    "premium_dist": abs(mid - ceiling),          # closer to ceiling = better convexity
                    "atm_dist": abs(float(r["strike"]) - underlying_price),
                    "gamma_score": abs(gamma),                   # maximize gamma
                    "delta_score": abs(delta - target_delta),    # minimize distance to target delta
                }
            )

        if not enriched:
            return None

        enriched.sort(
            key=lambda r: (
                r["premium_dist"],
                r["atm_dist"],
                -r["gamma_score"],
                r["delta_score"],
                -(r["_recv_ts"] or 0),
            )
        )

        best = enriched[0]

        return {
            "symbol": best["symbol"],
            "strike": float(best["strike"]),
            "right": side,
            "premium": round(best["mid"], 2),
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "contract": best["contract"],
            "_recv_ts": best["_recv_ts"],
        }
