"""
WS-Native StrikeSelector — convexity-optimized strike selection.

Selects:
    • Correct option side (CALL/PUT)
    • Valid quoted contracts
    • ATM ± 2 cluster
    • Mid <= $1.00 (maximum convexity)
    • Closest to $1 wins
    • Tie-break → closest to ATM
"""
class StrikeSelector:
    PREMIUM_CEILING = 1.00
    MAX_ATM_DISTANCE = 2

    def __init__(self, chain_bridge=None, engine=None):
        self.engine = engine  # reads last underlying price

    # -----------------------------------------------------------
    def _cluster_strikes(self, underlying_price: float, strikes: list):
        if underlying_price is None or not strikes:
            return []

        atm = min(strikes, key=lambda k: abs(k - underlying_price))
        cluster = {atm}

        for k in range(1, self.MAX_ATM_DISTANCE + 1):
            if (atm - k) in strikes:
                cluster.add(atm - k)
            if (atm + k) in strikes:
                cluster.add(atm + k)

        return list(cluster)

    # -----------------------------------------------------------
    async def select_from_chain(self, chain_rows: list, bias: str):
        if not chain_rows:
            return None

        # 1) filter CALL/PUT
        side = "C" if bias.upper() == "CALL" else "P"
        rows = [r for r in chain_rows if r.get("right") == side]
        if not rows:
            return None

        # 2) must have valid bid/ask
        rows = [
            r for r in rows
            if r.get("bid") and r.get("ask") and r["bid"] > 0 and r["ask"] > 0
        ]
        if not rows:
            return None

        symbol = rows[0]["symbol"]
        underlying = self.engine.last_price.get(symbol)
        if underlying is None:
            return None

        # 3) ATM ± 2 strike cluster
        strikes = sorted({float(r["strike"]) for r in rows})
        cluster = self._cluster_strikes(underlying, strikes)
        rows = [r for r in rows if float(r["strike"]) in cluster]
        if not rows:
            return None

        # 4) midprice + premium ceiling
        enriched = []
        for r in rows:
            mid = (float(r["bid"]) + float(r["ask"])) / 2
            if mid <= 0 or mid > self.PREMIUM_CEILING:
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

        # 5) sort by convexity → closest to $1, then ATM proximity
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
