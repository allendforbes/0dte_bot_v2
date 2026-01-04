"""
Continuation Engine — VWAP-Holding Pullback Entries

Architecture:
    on_tick() fires CONTINUATION_UP / CONTINUATION_DN signals when:
    - A valid trend already exists
    - We get a VWAP-holding pullback
    - MA is reclaimed
    - Local high/low breaks again

REFACTOR NOTE:
    This engine is called ONLY when SessionMandate.allows_entry() == True.
    It does not make permission decisions.
    
    The orchestrator must gate calls to on_tick() behind:
        if not mandate.allows_entry():
            return
"""

import time
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot_0dte.strategy.session_mandate import SessionMandate


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
    
    PERMISSION MODEL:
        This engine assumes the caller has already verified
        SessionMandate.allows_entry() == True.
        
        It does NOT:
        - Detect regime
        - Check acceptance
        - Make permission decisions
    """

    def __init__(self, lookback_sec=30, cooldown_sec=20):
        self.state = ContinuationState()
        self.lookback_sec = lookback_sec
        self.cooldown_sec = cooldown_sec

    # ------------------------------------------------------------------
    def update_trend_flags(self, mandate: "SessionMandate"):
        """
        Update trend flags from SessionMandate.
        
        Called by orchestrator after mandate determination.
        
        Args:
            mandate: Current SessionMandate (may or may not allow entry)
        """
        if mandate is None or mandate.bias is None:
            # No bias → reset both flags and pullback state
            if self.state.in_trend_up or self.state.in_trend_dn:
                # Bias flip detected - reset pullback state
                self.state.pullback_high = None
                self.state.pullback_low = None
            self.state.in_trend_up = False
            self.state.in_trend_dn = False
        else:
            new_trend_up = (mandate.bias == "CALL")
            new_trend_dn = (mandate.bias == "PUT")
            
            # Detect bias flip and reset pullback state
            if new_trend_up != self.state.in_trend_up or new_trend_dn != self.state.in_trend_dn:
                self.state.pullback_high = None
                self.state.pullback_low = None
            
            self.state.in_trend_up = new_trend_up
            self.state.in_trend_dn = new_trend_dn

    # ------------------------------------------------------------------
    def on_tick(
        self,
        price: float,
        vwap: float,
        ma: float,
        ts: float,
        mandate: Optional["SessionMandate"] = None,
    ) -> Optional[str]:
        """
        Called every NBBO/underlying update.
        
        PRECONDITION: If mandate is provided, mandate.allows_entry() == True
        
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
        if s.in_trend_up and price >= vwap and ma >= vwap:
            
            # Initialize pullback high
            if s.pullback_high is None:
                s.pullback_high = price
                return None
            
            # Pullback phase (price below high)
            if price < s.pullback_high:
                return None
            
            # Breakout phase (price exceeds prior high)
            if price > s.pullback_high:
                s.last_signal_ts = ts
                s.pullback_high = None
                s.pullback_low = None
                return "CONTINUATION_UP"
        
        elif s.in_trend_up:
            # VWAP or MA condition failed - reset pullback
            s.pullback_high = None

        # ------------------------------------------------------------
        # CONTINUATION DOWN
        # ------------------------------------------------------------
        if s.in_trend_dn and price <= vwap and ma <= vwap:
            
            # Initialize pullback low
            if s.pullback_low is None:
                s.pullback_low = price
                return None
            
            # Pullback phase (price above low)
            if price > s.pullback_low:
                return None
            
            # Breakout phase (price exceeds prior low)
            if price < s.pullback_low:
                s.last_signal_ts = ts
                s.pullback_high = None
                s.pullback_low = None
                return "CONTINUATION_DN"
        
        elif s.in_trend_dn:
            # VWAP or MA condition failed - reset pullback
            s.pullback_low = None

        return None