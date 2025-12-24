"""
PATCHED: strike_selector.py
-----------------------------
Converts premium band quality gates to observability metadata.

Changes:
- Premium bands logged as metadata, not used to filter
- Strikes processed if liquid (bid/ask > 0)
- Sorting simplified: ATM distance + freshness only
- Returns quality metrics for post-entry assessment
"""

import datetime as dt


class StrikeSelector:

    MAX_ATM_DISTANCE = 2

    CORE = {"SPY", "QQQ"}
    MATMAN = {"AAPL", "AMZN", "META", "MSFT", "NVDA", "TSLA"}

    # ----------------------------------------------------------------------
    # Premium bands (metadata only, not filters)
    # ----------------------------------------------------------------------
    PREMIUM_BANDS = {
        "SPY":  (0.40, 1.00),
        "QQQ":  (0.40, 1.00),
        "AAPL": (0.40, 1.40),
        "AMZN": (0.60, 1.50),
        "META": (0.70, 1.80),
        "MSFT": (0.90, 2.00),
        "TSLA": (0.80, 1.60),
        "NVDA": (2.00, 99.00),
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
        """Return premium band for metadata (not filtering)."""
        return self.PREMIUM_BANDS.get(symbol, (0.40, 1.00))

    # ----------------------------------------------------------------------
    # INTERFACE COMPATIBILITY: select() wrapper
    # ----------------------------------------------------------------------
    async def select(self, *, symbol: str, underlying_price: float, bias: str, chain: list):
        """
        Orchestrator-compatible wrapper for select_from_chain().
        
        This method exists for interface compatibility with orchestrator callsite.
        Delegates to select_from_chain() with proper parameter mapping.
        
        Args:
            symbol: Underlying symbol (for metadata logging)
            underlying_price: Current underlying price
            bias: CALL or PUT
            chain: Chain rows (list of dicts)
        
        Returns:
            Strike dict with quality metadata, or None if no liquid strikes
        """
        return await self.select_from_chain(
            chain_rows=chain,
            bias=bias,
            underlying_price=underlying_price
        )

    # ----------------------------------------------------------------------
    async def select_from_chain(self, chain_rows, bias, underlying_price):
        """
        Select best available strike from chain.
        
        Filters ONLY on:
        - Existence (strike data present)
        - Liquidity (bid > 0, ask > 0)
        - Contract sanity (valid pricing)
        
        Does NOT filter on:
        - Premium bands (logged as metadata)
        - Greeks (logged as metadata)
        - Convexity (logged as metadata)
        
        Returns:
            Strike dict with quality metadata, or None if no liquid strikes
        """
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
            
            # ============================================================
            # AVAILABILITY FILTER ONLY
            # ============================================================
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            
            # ============================================================
            # METADATA: Premium band (NOT A FILTER)
            # ============================================================
            in_premium_band = (band_lo <= mid <= band_hi)
            
            # Log if outside band (observability)
            if self.logger and not in_premium_band:
                self.logger.log_event("premium_band_note", {
                    "symbol": symbol,
                    "strike": r["strike"],
                    "mid": round(mid, 2),
                    "band_lo": band_lo,
                    "band_hi": band_hi,
                })

            gamma = r.get("gamma") or 0.0
            delta = r.get("delta") or 0.0

            # ------------------------------------------------------------
            # Enrichment: ATM distance + metadata
            # ------------------------------------------------------------
            enriched.append(
                {
                    **r,
                    "mid": mid,
                    "atm_dist": abs(float(r["strike"]) - underlying_price),
                    # Metadata (not used for sorting)
                    "in_premium_band": in_premium_band,
                    "gamma": gamma,
                    "delta": delta,
                    "delta_distance": abs(delta - target_delta),
                }
            )

        if not enriched:
            # Log reason for no strikes
            if self.logger:
                self.logger.log_event("no_liquid_strikes", {
                    "symbol": symbol,
                    "bias": bias,
                    "cluster_size": len(cluster),
                    "total_strikes": len(rows),
                })
            return None

        # ------------------------------------------------------------
        # Sorting: ATM distance + freshness ONLY
        # ------------------------------------------------------------
        enriched.sort(
            key=lambda r: (
                r["atm_dist"],          # Close to ATM (availability)
                -(r["_recv_ts"] or 0),  # Freshest data
            )
        )

        best = enriched[0]

        # ------------------------------------------------------------
        # Return with quality metadata
        # ------------------------------------------------------------
        return {
            "symbol": best["symbol"],
            "strike": float(best["strike"]),
            "right": side,
            "premium": round(best["mid"], 2),
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "contract": best["contract"],
            "_recv_ts": best["_recv_ts"],
            
            # Quality metadata (for post-entry assessment)
            "in_premium_band": best["in_premium_band"],
            "gamma": best["gamma"],
            "delta": best["delta"],
            "delta_distance": best["delta_distance"],
            "atm_distance": best["atm_dist"],
        }


# ======================================================================
# ORCHESTRATOR USAGE EXAMPLE
# ======================================================================
"""
# In orchestrator _evaluate_entry():

strike_result = await self.selector.select_from_chain(
    chain_rows=chain_rows,
    bias=signal.bias,
    underlying_price=price
)

if not strike_result:
    # No liquid strikes available (true unavailability)
    self.logger.log_event("strike_selection_failed", {"symbol": symbol})
    reason = "no_liquid_strikes"
    # Log HOLD, not BLOCK
    self.decision_log.log(
        decision="HOLD",
        symbol=symbol,
        reason=reason,
        ...
    )
    return

# Log quality metadata (observability)
self.logger.log_event("strike_quality", {
    "symbol": symbol,
    "strike": strike_result["strike"],
    "premium": strike_result["premium"],
    "in_premium_band": strike_result["in_premium_band"],
    "gamma": strike_result["gamma"],
    "delta": strike_result["delta"],
})

# Proceed with entry (quality doesn't block)
await self._execute_entry(symbol, signal, strike_result, qty)
"""