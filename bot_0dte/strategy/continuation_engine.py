"""
Continuation Engine v2.0 — VWAP-Holding Pullback Entries (REFACTORED)

Architecture:
    on_tick() fires CONTINUATION_UP / CONTINUATION_DN signals when:
    - A valid trend already exists
    - We get a VWAP-holding pullback
    - MA is reclaimed
    - Local high/low breaks again

REFACTORED:
    - Called ONLY when SessionMandate.allows_entry() == True
    - Does not make permission decisions
    - Enhanced state tracking
    - Proper bias flip handling

The orchestrator must gate calls to on_tick() behind:
    if not mandate.allows_entry():
        return
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from bot_0dte.strategy.session_mandate import SessionMandate

logger = logging.getLogger(__name__)


@dataclass
class ContinuationState:
    """
    State for continuation signal detection.
    
    Tracks trend direction, pullback levels, and signal timing.
    """
    # Trend flags
    in_trend_up: bool = False
    in_trend_dn: bool = False

    # Pullback tracking
    pullback_low: Optional[float] = None
    pullback_high: Optional[float] = None
    
    # Pullback extremes (for range tracking)
    pullback_extreme_low: Optional[float] = None
    pullback_extreme_high: Optional[float] = None

    # Timing
    last_reclaim_ts: float = 0
    last_signal_ts: float = 0
    
    # Statistics
    signals_fired: int = 0
    pullbacks_detected: int = 0
    
    def reset_pullback(self):
        """Reset pullback state."""
        self.pullback_low = None
        self.pullback_high = None
        self.pullback_extreme_low = None
        self.pullback_extreme_high = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "in_trend_up": self.in_trend_up,
            "in_trend_dn": self.in_trend_dn,
            "pullback_low": self.pullback_low,
            "pullback_high": self.pullback_high,
            "last_signal_ts": self.last_signal_ts,
            "signals_fired": self.signals_fired,
            "pullbacks_detected": self.pullbacks_detected,
        }


class ContinuationEngine:
    """
    A2-M CONTINUATION ENGINE (REFACTORED)
    -------------------------
    Fires CONTINUATION_UP / CONTINUATION_DN signals when:

      • A valid trend already exists
      • We get a VWAP-holding pullback
      • MA is reclaimed
      • Local high/low breaks again

    Does NOT double-fire. Hard time lockouts.
    
    PERMISSION MODEL:
        This engine assumes the caller has already verified
        SessionMandate.allows_entry() == True.
        
        It does NOT:
        - Detect regime
        - Check acceptance
        - Make permission decisions
    
    REFACTORED:
        - Proper bias flip handling with state reset
        - Enhanced pullback tracking
        - Better observability
    """

    # Configuration
    DEFAULT_LOOKBACK_SEC = 30
    DEFAULT_COOLDOWN_SEC = 20
    
    # Minimum pullback depth before breakout is valid
    MIN_PULLBACK_DEPTH_PCT = 0.001  # 0.1%

    def __init__(
        self, 
        lookback_sec: int = DEFAULT_LOOKBACK_SEC, 
        cooldown_sec: int = DEFAULT_COOLDOWN_SEC,
        log_func=None,
    ):
        """
        Initialize continuation engine.
        
        Args:
            lookback_sec: Lookback window for trend (not currently used)
            cooldown_sec: Minimum time between signals
            log_func: Optional logging function
        """
        self.state = ContinuationState()
        self.lookback_sec = lookback_sec
        self.cooldown_sec = cooldown_sec
        self._log_func = log_func or (lambda msg: logger.debug(msg))
    
    def _log(self, msg: str):
        self._log_func(msg)

    def update_trend_flags(self, mandate: Optional["SessionMandate"]):
        """
        Update trend flags from SessionMandate.
        
        Called by orchestrator after mandate determination.
        
        Args:
            mandate: Current SessionMandate (may or may not allow entry)
        """
        if mandate is None or mandate.bias is None:
            # No bias → reset both flags and pullback state
            if self.state.in_trend_up or self.state.in_trend_dn:
                self._log("[CONT] Bias cleared, resetting state")
                self.state.reset_pullback()
            self.state.in_trend_up = False
            self.state.in_trend_dn = False
        else:
            new_trend_up = (mandate.bias == "CALL")
            new_trend_dn = (mandate.bias == "PUT")
            
            # Detect bias flip and reset pullback state
            if new_trend_up != self.state.in_trend_up or new_trend_dn != self.state.in_trend_dn:
                self._log(f"[CONT] Bias flip detected: {mandate.bias}")
                self.state.reset_pullback()
            
            self.state.in_trend_up = new_trend_up
            self.state.in_trend_dn = new_trend_dn

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
        
        Args:
            price: Current price
            vwap: Current VWAP
            ma: Current moving average (e.g., 9-period)
            ts: Current timestamp (monotonic)
            mandate: Optional mandate for additional context
        
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
            # Must be above VWAP and MA
            if price >= vwap and ma >= vwap:
                
                # Initialize pullback high
                if s.pullback_high is None:
                    s.pullback_high = price
                    s.pullback_extreme_low = price
                    self._log(f"[CONT_UP] Init pullback_high={price:.2f}")
                    return None
                
                # Track pullback low
                if s.pullback_extreme_low is None or price < s.pullback_extreme_low:
                    s.pullback_extreme_low = price
                
                # Pullback phase (price below high)
                if price < s.pullback_high:
                    return None
                
                # Breakout phase (price exceeds prior high)
                if price > s.pullback_high:
                    # Validate pullback depth
                    if s.pullback_extreme_low and s.pullback_high:
                        depth = (s.pullback_high - s.pullback_extreme_low) / s.pullback_high
                        if depth < self.MIN_PULLBACK_DEPTH_PCT:
                            # Update high, wait for deeper pullback
                            s.pullback_high = price
                            return None
                    
                    s.last_signal_ts = ts
                    s.signals_fired += 1
                    s.pullbacks_detected += 1
                    
                    self._log(
                        f"[CONT_UP] SIGNAL price={price:.2f} "
                        f"prev_high={s.pullback_high:.2f}"
                    )
                    
                    s.reset_pullback()
                    return "CONTINUATION_UP"
            
            else:
                # VWAP or MA condition failed - reset pullback
                if s.pullback_high is not None:
                    self._log("[CONT_UP] Conditions lost, resetting pullback")
                s.pullback_high = None
                s.pullback_extreme_low = None

        # ------------------------------------------------------------
        # CONTINUATION DOWN
        # ------------------------------------------------------------
        if s.in_trend_dn:
            # Must be below VWAP and MA
            if price <= vwap and ma <= vwap:
                
                # Initialize pullback low
                if s.pullback_low is None:
                    s.pullback_low = price
                    s.pullback_extreme_high = price
                    self._log(f"[CONT_DN] Init pullback_low={price:.2f}")
                    return None
                
                # Track pullback high
                if s.pullback_extreme_high is None or price > s.pullback_extreme_high:
                    s.pullback_extreme_high = price
                
                # Pullback phase (price above low)
                if price > s.pullback_low:
                    return None
                
                # Breakout phase (price exceeds prior low)
                if price < s.pullback_low:
                    # Validate pullback depth
                    if s.pullback_extreme_high and s.pullback_low:
                        depth = (s.pullback_extreme_high - s.pullback_low) / s.pullback_low
                        if depth < self.MIN_PULLBACK_DEPTH_PCT:
                            # Update low, wait for deeper pullback
                            s.pullback_low = price
                            return None
                    
                    s.last_signal_ts = ts
                    s.signals_fired += 1
                    s.pullbacks_detected += 1
                    
                    self._log(
                        f"[CONT_DN] SIGNAL price={price:.2f} "
                        f"prev_low={s.pullback_low:.2f}"
                    )
                    
                    s.reset_pullback()
                    return "CONTINUATION_DN"
            
            else:
                # VWAP or MA condition failed - reset pullback
                if s.pullback_low is not None:
                    self._log("[CONT_DN] Conditions lost, resetting pullback")
                s.pullback_low = None
                s.pullback_extreme_high = None

        return None
    
    def get_state(self) -> Dict[str, Any]:
        """Get current engine state for debugging."""
        return {
            "state": self.state.to_dict(),
            "config": {
                "lookback_sec": self.lookback_sec,
                "cooldown_sec": self.cooldown_sec,
                "min_pullback_depth_pct": self.MIN_PULLBACK_DEPTH_PCT,
            },
        }
    
    def reset(self):
        """Reset engine state."""
        self.state = ContinuationState()
        self._log("[CONT] Engine reset")