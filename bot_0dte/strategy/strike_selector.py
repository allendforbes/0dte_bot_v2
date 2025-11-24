"""
WS-Native StrikeSelector — Convexity-Focused Strike Selection

Consumes normalized chain rows from ChainAggregator (built from NBBO).
No chain_bridge, no IBKR fallback, pure WS-native.

Selection Logic:
    1. Filter CALL/PUT by bias
    2. Reject rows with missing bid/ask
    3. Determine ATM from underlying price
    4. Build ATM ±2 cluster
    5. Calculate mid and apply premium <= $1 ceiling
    6. Select contract closest to $1 (maximum convexity)
    7. Tiebreak by strike proximity to ATM (closer is better)

Input chain row format (from ChainAggregator):
{
    "symbol": "SPY",
    "strike": 450.0,
    "right": "C" | "P",
    "premium": 0.95,  # mid price
    "bid": 0.90,
    "ask": 1.00,
    "contract": "O:SPY241122C00450000"
}

Output format:
{
    "symbol": "SPY",
    "strike": 450.0,
    "right": "C",
    "premium": 0.95,
    "bid": 0.90,
    "ask": 1.00,
    "contract": "O:SPY241122C00450000"
}
"""


class StrikeSelector:
    """
    WS-native strike selector.

    Selects optimal strike for maximum convexity (~$1 premium).
    """

    PREMIUM_CEILING = 1.00
    MAX_ATM_DISTANCE = 2  # ATM ±2

    def __init__(self, chain_bridge=None, engine=None):
        """
        Args:
            chain_bridge: DEPRECATED (kept for compatibility, not used)
            engine: ExecutionEngine (used to read last_price)
        """
        self.engine = engine

    # ------------------------------------------------------------------
    def _cluster_strikes(self, underlying_price: float, strikes: list) -> list:
        """
        Generate ATM ±2 strike cluster.

        Args:
            underlying_price: Current underlying price
            strikes: Available strikes (sorted)

        Returns:
            List of strikes in ATM ±2 range
        """
        if underlying_price is None or not strikes:
            return []

        # Find ATM strike (closest to underlying)
        atm = min(strikes, key=lambda k: abs(k - underlying_price))
        cluster = [atm]

        # Add ATM±1, ATM±2
        for k in range(1, self.MAX_ATM_DISTANCE + 1):
            if (atm - k) in strikes:
                cluster.append(atm - k)
            if (atm + k) in strikes:
                cluster.append(atm + k)

        return cluster

    # ------------------------------------------------------------------
    async def select_from_chain(self, chain_rows: list, bias: str) -> dict | None:
        """
        Select optimal strike from WS-native chain rows.

        Args:
            chain_rows: List of normalized option dicts from ChainAggregator
            bias: "CALL" or "PUT"

        Returns:
            Selected strike dict or None if no suitable option found
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
        # Must have valid bid/ask, discard missing quotes
        # ---------------------------------------------------------------
        priced = [
            r
            for r in rows
            if r.get("bid") is not None
            and r.get("ask") is not None
            and r.get("bid") > 0
            and r.get("ask") > 0
        ]
        if not priced:
            return None

        # ---------------------------------------------------------------
        # 3 — UNDERLYING PRICE
        # Used to determine ATM strike
        # ---------------------------------------------------------------
        symbol = priced[0].get("symbol")
        underlying = self.engine.last_price.get(symbol)
        if underlying is None:
            return None

        # ---------------------------------------------------------------
        # 4 — ATM ±2 STRIKE CLUSTER
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
        # Calculate mid from bid/ask, filter by ceiling
        # ---------------------------------------------------------------
        enriched = []
        for r in clustered_rows:
            bid = float(r.get("bid", 0.0))
            ask = float(r.get("ask", 0.0))
            mid = (bid + ask) / 2

            # Skip invalid or too expensive options
            if mid <= 0:
                continue
            if mid > self.PREMIUM_CEILING:
                continue

            enriched.append(
                {
                    **r,
                    "mid": mid,
                    "dist": abs(mid - self.PREMIUM_CEILING),  # Distance from $1
                    "atm_dist": abs(
                        float(r["strike"]) - underlying
                    ),  # Distance from ATM
                }
            )

        if not enriched:
            return None

        # ---------------------------------------------------------------
        # 6 — SORT BY:
        #      1) Closest to $1 premium (maximum convexity)
        #      2) Closest to ATM (tiebreaker)
        # ---------------------------------------------------------------
        enriched.sort(key=lambda r: (r["dist"], r["atm_dist"]))
        best = enriched[0]

        # ---------------------------------------------------------------
        # 7 — RETURN NORMALIZED STRIKE
        # ---------------------------------------------------------------
        return {
            "symbol": symbol,
            "strike": float(best["strike"]),
            "right": side,
            "premium": round(best["mid"], 2),
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "contract": best["contract"],
        }
