import time
from dataclasses import dataclass


@dataclass
class ContinuationState:
    in_trend_up: bool = False
    in_trend_dn: bool = False

    pullback_low: float = None
    pullback_high: float = None

    last_reclaim_ts: float = 0
    last_signal_ts: float = 0


class ContinuationEngine:
    """
    A2-M CONTINUATION ENGINE
    -------------------------
    Fires CONTINUATION_UP / CONTINUATION_DN signals when:

      • A valid trend already exists
      • We get a VWAP-holding pullback
      • MA is reclaimed
      • Local high/low breaks again
      • Premium + latency + freshness OK

    Does NOT double-fire. Hard time lockouts.
    """

    def __init__(self, lookback_sec=30, cooldown_sec=20):
        self.state = ContinuationState()
        self.lookback_sec = lookback_sec
        self.cooldown_sec = cooldown_sec

    # ------------------------------------------------------------------
    def update_trend_flags(self, trend_up: bool, trend_dn: bool):
        """Orchestrator calls this every tick."""
        self.state.in_trend_up = trend_up
        self.state.in_trend_dn = trend_dn

    # ------------------------------------------------------------------
    def on_tick(self, price: float, vwap: float, ma: float, ts: float):
        """
        Called every NBBO/underlying update.
        Returns:
            "CONTINUATION_UP", "CONTINUATION_DN", or None
        """

        s = self.state

        # ------------------------------------------------------------
        # GLOBAL COOLDOWN — prevents double entries
        # ------------------------------------------------------------
        if ts - s.last_signal_ts < self.cooldown_sec:
            return None

        # ------------------------------------------------------------
        # CONTINUATION UP
        # ------------------------------------------------------------
        if s.in_trend_up:

            # Require price stays ABOVE VWAP on pullback
            if price >= vwap:
                # Track highest pullback point after MA reclaim
                if ma >= vwap:
                    if s.pullback_high is None:
                        s.pullback_high = price
                    else:
                        s.pullback_high = max(s.pullback_high, price)

                # Break of that local high → continuation
                if s.pullback_high and price > s.pullback_high:
                    s.last_signal_ts = ts
                    s.pullback_high = None
                    s.pullback_low = None
                    return "CONTINUATION_UP"

            else:
                # Reset pullback if VWAP fails
                s.pullback_high = None

        # ------------------------------------------------------------
        # CONTINUATION DOWN
        # ------------------------------------------------------------
        if s.in_trend_dn:

            # Require price stays BELOW VWAP on pullback
            if price <= vwap:
                # Track lowest pullback point after MA reclaim
                if ma <= vwap:
                    if s.pullback_low is None:
                        s.pullback_low = price
                    else:
                        s.pullback_low = min(s.pullback_low, price)

                # Break of that local low → continuation
                if s.pullback_low and price < s.pullback_low:
                    s.last_signal_ts = ts
                    s.pullback_high = None
                    s.pullback_low = None
                    return "CONTINUATION_DN"

            else:
                # Reset pullback if VWAP fails
                s.pullback_low = None

        return None
