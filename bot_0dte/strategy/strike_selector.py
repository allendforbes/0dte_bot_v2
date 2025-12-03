class StrikeSelector:
    PREMIUM_CEILING = 1.00
    MAX_ATM_DISTANCE = 2

    def __init__(self, chain_bridge=None, engine=None):
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
    # MAIN SELECTION API (greeks + timestamp aware)
    # ---------------------------------------------------------
    async def select_from_chain(self, chain_rows: list, bias: str, underlying_price: float = None):
        if not chain_rows or underlying_price is None:
            return None

        side = "C" if bias.upper() == "CALL" else "P"

        # Step 1 — filter by right
        rows = [r for r in chain_rows if r.get("right") == side]
        if not rows:
            return None

        # Step 2 — valid quotes only
        rows = [r for r in rows if r.get("bid") and r.get("ask") and r["bid"] > 0 and r["ask"] > 0]
        if not rows:
            return None

        symbol = rows[0]["symbol"]

        # Step 3 — ATM clustering
        strikes = sorted({float(r["strike"]) for r in rows})
        cluster = self._cluster_strikes(underlying_price, strikes)
        rows = [r for r in rows if float(r["strike"]) in cluster]
        if not rows:
            return None

        enriched = []
        for r in rows:
            mid = (r["bid"] + r["ask"]) / 2
            if mid <= 0 or mid > self.PREMIUM_CEILING:
                continue

            enriched.append({
                **r,
                "mid": mid,
                "dist": abs(mid - self.PREMIUM_CEILING),
                "atm_dist": abs(float(r["strike"]) - underlying_price),
                "gamma_score": abs(r.get("gamma") or 0),
                "delta_score": abs((r.get("delta") or 0) - (0.30 if side == "C" else -0.30)),
                "_recv_ts": r.get("_recv_ts"),
            })

        if not enriched:
            return None

        # -----------------------------------------------------
        # FINAL SCORING WEIGHTS:
        #   1) convexity (mid → $1)
        #   2) ATM proximity
        #   3) gamma (prefer higher)
        #   4) delta targeting (prefer ~0.30)
        #   5) NBBO recency (prefer freshest)
        # -----------------------------------------------------
        enriched.sort(
            key=lambda r: (
                r["dist"],
                r["atm_dist"],
                -r["gamma_score"],
                r["delta_score"],
                -(r["_recv_ts"] or 0)
            )
        )

        best = enriched[0]

        return {
            "symbol": symbol,
            "strike": float(best["strike"]),
            "right": side,
            "premium": round(best["mid"], 2),
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "contract": best["contract"],
            "iv": best.get("iv"),
            "delta": best.get("delta"),
            "gamma": best.get("gamma"),
            "theta": best.get("theta"),
            "vega": best.get("vega"),
            "volume": best.get("volume"),
            "open_interest": best.get("open_interest"),
            "_recv_ts": best.get("_recv_ts"),
        }
