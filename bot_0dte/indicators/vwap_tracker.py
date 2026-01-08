"""
VWAP Tracker v2.0 (REFACTORED)

FIXES:
    - Proper price-volume weighted calculation
    - Returns structured VWAPResult with dev and slope
    - Never returns silent 0.0 fallback
    - Tracks deviation change (slope) for regime detection
    - Includes volume validation

Architecture:
    VWAPTracker.update(price, volume) → VWAPResult
    
    Each symbol should have its own tracker instance.
    Results include vwap, deviation, and slope for mandate engine.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class VWAPResult:
    """
    Structured VWAP calculation result.
    
    All fields are always populated (never silent fallbacks).
    """
    vwap: float  # Volume-weighted average price
    vwap_dev: float  # Current price - VWAP
    vwap_dev_change: float  # Change in deviation (slope indicator)
    price: float  # Input price (for reference)
    volume: float  # Input volume
    total_volume: float  # Cumulative volume
    tick_count: int  # Number of ticks processed
    is_valid: bool  # True if calculation is reliable
    
    def to_dict(self):
        return {
            "vwap": round(self.vwap, 4),
            "vwap_dev": round(self.vwap_dev, 4),
            "vwap_dev_change": round(self.vwap_dev_change, 6),
            "price": round(self.price, 4),
            "volume": self.volume,
            "total_volume": self.total_volume,
            "tick_count": self.tick_count,
            "is_valid": self.is_valid,
        }


class VWAPTracker:
    """
    Per-symbol VWAP tracker with proper volume weighting.
    
    FIXES:
        - Never returns silent 0.0 for vwap_dev
        - Tracks deviation change for slope detection
        - Validates volume input
        - Marks result as invalid if insufficient data
    
    Usage:
        tracker = VWAPTracker(symbol="SPY")
        result = tracker.update(price=450.50, volume=1000)
        if result.is_valid:
            print(f"VWAP: {result.vwap}, Dev: {result.vwap_dev}")
    """
    
    # Minimum ticks before VWAP is considered valid
    MIN_TICKS_FOR_VALID = 5
    
    # Minimum total volume before VWAP is considered valid
    MIN_VOLUME_FOR_VALID = 100
    
    # Window size for rolling calculation (0 = session VWAP)
    DEFAULT_WINDOW_SIZE = 0  # Full session
    
    def __init__(
        self, 
        symbol: str = "",
        window_size: int = 0,
        log_func=None,
    ):
        """
        Initialize VWAP tracker.
        
        Args:
            symbol: Symbol for logging
            window_size: Rolling window size (0 = full session)
            log_func: Optional logging function
        """
        self.symbol = symbol
        self.window_size = window_size
        self._log_func = log_func or (lambda msg: logger.debug(msg))
        
        # Accumulation state
        self._cum_pv: float = 0.0  # Cumulative price * volume
        self._cum_vol: float = 0.0  # Cumulative volume
        self._tick_count: int = 0
        
        # Rolling window (if window_size > 0)
        self._window: List[Tuple[float, float]] = []  # [(price, volume), ...]
        
        # Deviation tracking
        self._last_vwap: Optional[float] = None
        self._last_dev: float = 0.0
        
    def _log(self, msg: str):
        self._log_func(msg)
    
    def reset(self):
        """Reset tracker for new session."""
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self._tick_count = 0
        self._window.clear()
        self._last_vwap = None
        self._last_dev = 0.0
        self._log(f"[VWAP] {self.symbol} tracker RESET")
    
    def update(self, price: float, volume: float = 1.0) -> VWAPResult:
        """
        Update VWAP with new price/volume tick.
        
        FIX #4: Never returns silent 0.0 for vwap_dev.
        
        Args:
            price: Current price
            volume: Trade volume (default 1.0 for price-only feeds)
        
        Returns:
            VWAPResult with all calculations
        """
        
        # Validate inputs
        if price <= 0:
            self._log(f"[VWAP] {self.symbol} invalid price={price}")
            return VWAPResult(
                vwap=self._last_vwap or price,
                vwap_dev=0.0,
                vwap_dev_change=0.0,
                price=price,
                volume=volume,
                total_volume=self._cum_vol,
                tick_count=self._tick_count,
                is_valid=False,
            )
        
        # Use minimum volume of 1 if zero/negative provided
        vol = max(volume, 1.0)
        
        # Rolling window mode
        if self.window_size > 0:
            self._window.append((price, vol))
            if len(self._window) > self.window_size:
                self._window.pop(0)
            
            # Calculate from window
            cum_pv = sum(p * v for p, v in self._window)
            cum_vol = sum(v for _, v in self._window)
        else:
            # Session VWAP mode
            self._cum_pv += price * vol
            self._cum_vol += vol
            cum_pv = self._cum_pv
            cum_vol = self._cum_vol
        
        self._tick_count += 1
        
        # Calculate VWAP
        if cum_vol > 0:
            vwap = cum_pv / cum_vol
        else:
            vwap = price  # Fallback to price if no volume
        
        # Calculate deviation
        vwap_dev = price - vwap
        
        # Calculate deviation change (slope)
        vwap_dev_change = vwap_dev - self._last_dev
        
        # Determine validity
        is_valid = (
            self._tick_count >= self.MIN_TICKS_FOR_VALID and
            cum_vol >= self.MIN_VOLUME_FOR_VALID
        )
        
        # Update state
        self._last_vwap = vwap
        self._last_dev = vwap_dev
        
        # Log for debugging
        self._log(
            f"[VWAP] {self.symbol} price={price:.2f} vol={vol:.0f} "
            f"vwap={vwap:.2f} dev={vwap_dev:+.4f} slope={vwap_dev_change:+.6f} "
            f"valid={is_valid}"
        )
        
        return VWAPResult(
            vwap=vwap,
            vwap_dev=vwap_dev,
            vwap_dev_change=vwap_dev_change,
            price=price,
            volume=vol,
            total_volume=cum_vol,
            tick_count=self._tick_count,
            is_valid=is_valid,
        )
    
    @property
    def current_vwap(self) -> Optional[float]:
        """Get current VWAP value (may be None if not yet calculated)."""
        return self._last_vwap
    
    @property
    def current_dev(self) -> float:
        """Get current deviation from VWAP."""
        return self._last_dev
    
    @property
    def is_valid(self) -> bool:
        """Check if tracker has enough data for valid VWAP."""
        return (
            self._tick_count >= self.MIN_TICKS_FOR_VALID and
            self._cum_vol >= self.MIN_VOLUME_FOR_VALID
        )
    
    def get_state(self) -> dict:
        """Get current tracker state for debugging."""
        return {
            "symbol": self.symbol,
            "vwap": self._last_vwap,
            "last_dev": self._last_dev,
            "cum_pv": self._cum_pv,
            "cum_vol": self._cum_vol,
            "tick_count": self._tick_count,
            "is_valid": self.is_valid,
            "window_size": self.window_size,
        }


class VWAPTrackerManager:
    """
    Manager for multiple symbol VWAP trackers.
    
    Provides centralized access to per-symbol trackers
    with automatic creation and session reset.
    """
    
    def __init__(self, window_size: int = 0, log_func=None):
        self._trackers: dict[str, VWAPTracker] = {}
        self._window_size = window_size
        self._log_func = log_func
    
    def get_tracker(self, symbol: str) -> VWAPTracker:
        """Get or create tracker for symbol."""
        if symbol not in self._trackers:
            self._trackers[symbol] = VWAPTracker(
                symbol=symbol,
                window_size=self._window_size,
                log_func=self._log_func,
            )
        return self._trackers[symbol]
    
    def update(self, symbol: str, price: float, volume: float = 1.0) -> VWAPResult:
        """Update VWAP for symbol."""
        tracker = self.get_tracker(symbol)
        return tracker.update(price, volume)
    
    def reset_all(self):
        """Reset all trackers (new session)."""
        for tracker in self._trackers.values():
            tracker.reset()
    
    def reset_symbol(self, symbol: str):
        """Reset single symbol tracker."""
        if symbol in self._trackers:
            self._trackers[symbol].reset()
    
    def get_all_states(self) -> dict:
        """Get state of all trackers."""
        return {
            symbol: tracker.get_state()
            for symbol, tracker in self._trackers.items()
        }