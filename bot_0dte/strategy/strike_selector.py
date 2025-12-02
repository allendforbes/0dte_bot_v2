class StrikeSelector:
    PREMIUM_CEILING = 1.00
    MAX_ATM_DISTANCE = 2

    def __init__(self, chain_bridge=None, engine=None):
        # StrikeSelector no longer depends on engine.last_price
        pass

    def _cluster_strikes(self, underlying_price: float, strikes: list):
        """Return ATM ± MAX_ATM_DISTANCE strike cluster."""
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

    # ---------------------------------------------------------
    # MAIN SELECTION API  (note underlying_price added)
    # ---------------------------------------------------------
    async def select_from_chain(self, chain_rows: list, bias: str, underlying_price: float = None):
        """
        Select optimal strike given the chain snapshot and underlying price.
        """
        if not chain_rows or underlying_price is None:
            return None

        side = "C" if bias.upper() == "CALL" else "P"

        # Step 1 — filter by CALL / PUT
        rows = [r for r in chain_rows if r.get("right") == side]
        if not rows:
            return None

        # Step 2 — require valid bid/ask
        rows = [
            r for r in rows
            if r.get("bid") and r.get("ask") and r["bid"] > 0 and r["ask"] > 0
        ]
        if not rows:
            return None

        symbol = rows[0]["symbol"]

        # Step 3 — strike clustering
        strikes = sorted({float(r["strike"]) for r in rows})
        cluster = self._cluster_strikes(underlying_price, strikes)
        rows = [r for r in rows if float(r["strike"]) in cluster]
        if not rows:
            return None

        # Step 4 — keep mid ≤ $1.00 ceiling
        enriched = []
        for r in rows:
            mid = (float(r["bid"]) + float(r["ask"])) / 2
            if mid <= 0 or mid > self.PREMIUM_CEILING:
                continue

            enriched.append({
                **r,
                "mid": mid,
                "dist": abs(mid - self.PREMIUM_CEILING),
                "atm_dist": abs(float(r["strike"]) - underlying_price),
            })

        if not enriched:
            return None

        # Step 5 — sort by convexity → closest to $1 AND ATM
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
