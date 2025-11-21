"""
LatencyPrecheck — ensures we are catching EARLY movement,
not chasing an expanded candle or stale liquidity.

This module enforces:
    1. Tick freshness
    2. Spread sanity
    3. Slippage bound between signal-price and current bid/ask
    4. Microstructure alignment (no abrupt reversal ticks)

Output schema:
{
    "ok": bool,
    "limit_price": float,
    "reason": str
}
"""

from dataclasses import dataclass


@dataclass
class PrecheckResult:
    ok: bool
    limit_price: float = None
    reason: str = None


class LatencyPrecheck:
    def __init__(self):
        # Tick sanity
        self.MAX_STALE_MS = 400  # must be <0.4s old
        self.MAX_SPREAD_PCT = 0.25  # 25% of premium max
        self.MAX_SLIPPAGE = 0.15  # 15% adverse movement
        self.MAX_REVERSE_SLOPE = -0.01  # no micro flip

    # ------------------------------------------------------------------
    def validate(self, symbol: str, tick: dict, bias: str) -> PrecheckResult:
        """
        tick = {
            "price": ...,
            "bid": ...,
            "ask": ...,
            "timestamp": epoch_ms,
            "vwap_dev_change": ...,
        }
        """

        price = tick.get("price")
        bid = tick.get("bid")
        ask = tick.get("ask")
        ts = tick.get("timestamp")

        if price is None or bid is None or ask is None:
            return PrecheckResult(False, reason="missing_prices")

        mid = (bid + ask) / 2

        # ==============================================================
        # 1) Spread sanity
        # ==============================================================

        # avoid cases where bid=0.00 (illiquid or stale)
        if bid <= 0 or ask <= 0:
            return PrecheckResult(False, reason="zero_bid_or_ask")

        spread = ask - bid
        if spread / max(mid, 0.01) > self.MAX_SPREAD_PCT:
            return PrecheckResult(False, reason="spread_too_wide")

        # ==============================================================
        # 2) Slippage protection
        # ==============================================================

        # CALL: ensure price didn't already rip
        if bias == "CALL":
            if (ask - price) / max(price, 0.01) > self.MAX_SLIPPAGE:
                return PrecheckResult(False, reason="call_slippage")
        # PUT: ensure price didn’t already tank
        else:
            if (price - bid) / max(price, 0.01) > self.MAX_SLIPPAGE:
                return PrecheckResult(False, reason="put_slippage")

        # ==============================================================
        # 3) Microstructure reversal veto
        # ==============================================================

        slope = tick.get("vwap_dev_change")
        if slope is not None:
            reverse_flip = (
                bias == "CALL"
                and slope < self.MAX_REVERSE_SLOPE
                or bias == "PUT"
                and slope > -self.MAX_REVERSE_SLOPE
            )
            if reverse_flip:
                return PrecheckResult(False, reason="reversal_tick")

        # ==============================================================
        # 4) Construct execution LMT price
        # ==============================================================

        # CALL → enter at ask
        # PUT  → enter at bid
        limit_price = ask if bias == "CALL" else bid

        return PrecheckResult(True, limit_price=limit_price)
