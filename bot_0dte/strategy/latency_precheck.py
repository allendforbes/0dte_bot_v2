"""
LatencyPrecheck — Ensures Early Movement Detection

WS-Native Compatible:
    • No ms timestamp requirement (removed)
    • Accepts _recv_ts (seconds) if available
    • Guards vwap_dev_change with default 0
    • Freshness guaranteed by WS heartbeat watchdog

This module enforces:
    1. Spread sanity
    2. Slippage bound between signal-price and current bid/ask
    3. Microstructure alignment (no abrupt reversal ticks)

Output schema:
{
    "ok": bool,
    "limit_price": float | None,
    "reason": str | None
}
"""

from dataclasses import dataclass


@dataclass
class PrecheckResult:
    """Result object for latency pre-check."""

    ok: bool
    limit_price: float = None
    reason: str = None


class LatencyPrecheck:
    """
    WS-native latency pre-check.

    Validates market conditions before entry without requiring
    precise timestamps (freshness guaranteed by WS adapter).
    """

    def __init__(self):
        # Spread sanity
        self.MAX_SPREAD_PCT = 0.25  # 25% of premium max

        # Slippage protection
        self.MAX_SLIPPAGE = 0.15  # 15% adverse movement

        # Microstructure reversal detection
        self.MAX_REVERSE_SLOPE = -0.01  # No micro flip

    # ------------------------------------------------------------------
    def validate(self, symbol: str, tick: dict, bias: str) -> PrecheckResult:
        """
        Validate market conditions for entry.

        Args:
            symbol: Trading symbol
            tick: Market data dict
            bias: "CALL" or "PUT"

        Tick format (from Orchestrator):
        {
            "price": float,
            "bid": float,
            "ask": float,
            "vwap_dev_change": float | None,  # Optional
            "_recv_ts": float | None,         # Optional (seconds)
        }

        Returns:
            PrecheckResult with ok flag, limit_price, and reason
        """

        # =====================================================
        # EXTRACT PRICES
        # =====================================================
        price = tick.get("price")
        bid = tick.get("bid")
        ask = tick.get("ask")

        if price is None or bid is None or ask is None:
            return PrecheckResult(False, reason="missing_prices")

        mid = (bid + ask) / 2

        # =====================================================
        # 1) SPREAD SANITY
        # =====================================================
        # Avoid cases where bid=0.00 (illiquid or stale)
        if bid <= 0 or ask <= 0:
            return PrecheckResult(False, reason="zero_bid_or_ask")

        spread = ask - bid
        if spread / max(mid, 0.01) > self.MAX_SPREAD_PCT:
            return PrecheckResult(False, reason="spread_too_wide")

        # =====================================================
        # 2) SLIPPAGE PROTECTION
        # =====================================================
        # CALL: ensure price didn't already rip
        if bias == "CALL":
            if (ask - price) / max(price, 0.01) > self.MAX_SLIPPAGE:
                return PrecheckResult(False, reason="call_slippage")
        # PUT: ensure price didn't already tank
        else:
            if (price - bid) / max(price, 0.01) > self.MAX_SLIPPAGE:
                return PrecheckResult(False, reason="put_slippage")

        # =====================================================
        # 3) MICROSTRUCTURE REVERSAL VETO
        # =====================================================
        # Only check if vwap_dev_change is available
        slope = tick.get("vwap_dev_change")

        if slope is not None and slope != 0:
            # Check for reversal against bias
            reverse_flip = (bias == "CALL" and slope < self.MAX_REVERSE_SLOPE) or (
                bias == "PUT" and slope > -self.MAX_REVERSE_SLOPE
            )

            if reverse_flip:
                return PrecheckResult(False, reason="reversal_tick")

        # =====================================================
        # 4) CONSTRUCT EXECUTION LIMIT PRICE
        # =====================================================
        # CALL → enter at ask
        # PUT  → enter at bid
        limit_price = ask if bias == "CALL" else bid

        return PrecheckResult(True, limit_price=limit_price)
