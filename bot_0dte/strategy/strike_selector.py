"""
StrikeSelector v3.3 — Dynamic Premium Bands + Convexity Prioritization
---------------------------------------------------------------------

Enhancements over v3.1:
    • Symbol-specific premium bands (realistic per underlying)
    • Bands enforce both lower + upper bounds
    • Prefers mid-band premiums for convexity efficiency
    • Excludes NVDA (no realistic <2.00 convex premium)
    • Adjusts MSFT upper band (MSFT is always premium-heavy)
    • Adds detailed rejection logging: premium_band_fail
    • Maintains:
        – Delta targeting
        – Gamma prioritization
        – ATM clustering logic (±2)
        – Freshest NBBO tie-breaker
"""

import datetime as dt


class StrikeSelector:

    MAX_ATM_DISTANCE = 2

    CORE = {"SPY", "QQQ"}
    MATMAN = {"AAPL", "AMZN", "META", "MSFT", "NVDA", "TSLA"}

    # ----------------------------------------------------------------------
    # Realistic premium bands (empirically tuned)
    # ----------------------------------------------------------------------
    PREMIUM_BANDS = {
        # SPY/QQQ — strict
        "SPY":  (0.40, 1.00),
        "QQQ":  (0.40, 1.00),

        # MATMAN
        "AAPL": (0.40, 1.40),
        "AMZN": (0.60, 1.50),
        "META": (0.70, 1.80),
        "MSFT": (0.90, 2.00),   # MSFT rarely prints cheap premium early
        "TSLA": (0.80, 1.60),

        # NVDA — effectively excluded
        "NVDA": (2.00, 99.00),  # NVDA is ALWAYS expensive
    }

    TARGET_DELTA_CALL = 0.30
    TARGET_DELTA_PUT  = -0.30

    # ----------------------------------------------------------------------
    def __init__(self, logger=None):
        self.logger = logger

    # ----------------------------------------------------------------------
    def _cluster_strikes(self, underlying, strikes):
        """Return ATM ±2 strike cluster."""
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

    # ----------------------------------------------------------------------
    def _premium_band_for(self, symbol):
        """Return realistic symbol-specific premium band."""
        return self.PREMIUM_BANDS.get(symbol, (0.40, 1.00))

    # ----------------------------------------------------------------------
    async def select_from_chain(self, chain_rows, bias, underlying_price):
        if not chain_rows or underlying_price is None:
            return None

        side = "C" if bias.upper() == "CALL" else "P"
        rows = [r for r in chain_rows if r["right"] == side]
        if not rows:
            return None

        symbol = rows[0]["symbol"]
        band_lo, band_hi = self._premium_band_for(symbol)

        # ATM clustering
        strikes = sorted({float(r["strike"]) for r in rows if r["strike"] is not None})
        cluster = self._cluster_strikes(underlying_price, strikes)
        if not cluster:
            return None

        # Filter to cluster
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

            # ============================================================
            # Premium band rejection
            # ============================================================
            if not (band_lo <= mid <= band_hi):
                if self.logger:
                    self.logger.log_event("premium_band_fail", {
                        "symbol": symbol,
                        "strike": r["strike"],
                        "mid": round(mid, 2),
                        "band_lo": band_lo,
                        "band_hi": band_hi,
                    })
                continue

            gamma = r.get("gamma") or 0.0
            delta = r.get("delta") or 0.0

            # ------------------------------------------------------------
            # Enrichment for sorting
            # ------------------------------------------------------------
            band_mid = (band_lo + band_hi) / 2
            band_dist = abs(mid - band_mid)  # prefer midpoint of band

            enriched.append(
                {
                    **r,
                    "mid": mid,
                    "band_dist": band_dist,
                    "atm_dist": abs(float(r["strike"]) - underlying_price),
                    "gamma_score": abs(gamma),
                    "delta_score": abs(delta - target_delta),
                }
            )

        if not enriched:
            return None

        # ------------------------------------------------------------
        # Sorting priorities (best → worst)
        # ------------------------------------------------------------
        enriched.sort(
            key=lambda r: (
                r["band_dist"],         # Prefer mid-band premium
                r["atm_dist"],          # Close to ATM
                -r["gamma_score"],      # High convexity
                r["delta_score"],       # Delta close to target
                -(r["_recv_ts"] or 0),  # Freshest data
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
