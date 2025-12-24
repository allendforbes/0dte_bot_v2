"""
PATCHED: latency_precheck.py
-------------------------------
Converts permission-based blocking to measurement-based observability.

Changes:
- validate() returns measurements dict (no "ok" boolean)
- Spread/slippage/reversal are metrics, not gates
- All measurements logged, none block entry
- limit_price still provided for execution
"""

from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class LatencyMetrics:
    """Execution quality measurements (observability only)."""
    
    spread_pct: float
    slippage_pct: float
    reversal_slope: float
    limit_price: float
    
    # Error field for missing data
    error: str = None


class LatencyPrecheck:
    """
    WS-native latency measurement.
    
    Measures market conditions for observability.
    Does NOT block entry - all measurements are logged.
    """

    def __init__(self):
        # Measurement thresholds (for logging severity)
        self.SPREAD_THRESHOLD = 0.25
        self.SLIPPAGE_THRESHOLD = 0.15
        self.REVERSAL_THRESHOLD = 0.01

    # ------------------------------------------------------------------
    def measure(self, symbol: str, tick: dict, bias: str) -> LatencyMetrics:
        """
        Measure execution quality metrics.
        
        Args:
            symbol: Trading symbol
            tick: Market data dict
            bias: "CALL" or "PUT"
        
        Tick format:
        {
            "price": float,
            "bid": float,
            "ask": float,
            "vwap_dev_change": float | None,
            "_recv_ts": float | None,
        }
        
        Returns:
            LatencyMetrics with measurements (never blocks)
        """
        
        # =====================================================
        # EXTRACT PRICES
        # =====================================================
        price = tick.get("price")
        bid = tick.get("bid")
        ask = tick.get("ask")
        
        if price is None or bid is None or ask is None:
            return LatencyMetrics(
                spread_pct=0.0,
                slippage_pct=0.0,
                reversal_slope=0.0,
                limit_price=None,
                error="missing_prices"
            )
        
        if bid <= 0 or ask <= 0:
            return LatencyMetrics(
                spread_pct=0.0,
                slippage_pct=0.0,
                reversal_slope=0.0,
                limit_price=None,
                error="zero_bid_or_ask"
            )
        
        mid = (bid + ask) / 2
        
        # =====================================================
        # MEASURE: Spread
        # =====================================================
        spread = ask - bid
        spread_pct = spread / max(mid, 0.01)
        
        # =====================================================
        # MEASURE: Slippage
        # =====================================================
        if bias == "CALL":
            slippage_pct = (ask - price) / max(price, 0.01)
        else:
            slippage_pct = (price - bid) / max(price, 0.01)
        
        # =====================================================
        # MEASURE: Reversal slope
        # =====================================================
        reversal_slope = tick.get("vwap_dev_change", 0.0)
        
        # =====================================================
        # CONSTRUCT LIMIT PRICE
        # =====================================================
        limit_price = ask if bias == "CALL" else bid
        
        # =====================================================
        # RETURN MEASUREMENTS (NO VETO)
        # =====================================================
        return LatencyMetrics(
            spread_pct=spread_pct,
            slippage_pct=slippage_pct,
            reversal_slope=reversal_slope,
            limit_price=limit_price
        )
    
    # ------------------------------------------------------------------
    # DEPRECATED: Keep for backward compatibility
    # ------------------------------------------------------------------
    def validate(self, symbol: str, tick: dict, bias: str) -> Dict[str, Any]:
        """
        DEPRECATED: Use measure() instead.
        
        Returns dict with measurements for backward compatibility.
        """
        metrics = self.measure(symbol, tick, bias)
        
        return {
            "spread_pct": metrics.spread_pct,
            "slippage_pct": metrics.slippage_pct,
            "reversal_slope": metrics.reversal_slope,
            "limit_price": metrics.limit_price,
            "error": metrics.error,
            # For backward compat: always "ok"
            "ok": True,
            "reason": metrics.error or "measured"
        }


# ======================================================================
# ORCHESTRATOR USAGE EXAMPLE
# ======================================================================
"""
# In orchestrator _evaluate_entry():

try:
    metrics = self.latency.measure(symbol, tick, signal.bias)
    
    # Log measurements (observability)
    self.logger.log_event("latency_metrics", {
        "symbol": symbol,
        "spread_pct": round(metrics.spread_pct * 100, 2),
        "slippage_pct": round(metrics.slippage_pct * 100, 2),
        "reversal_slope": round(metrics.reversal_slope, 4),
        "limit_price": metrics.limit_price,
        "error": metrics.error,
    })
    
    # Note: No branching on metrics - always proceed to entry
    
except Exception as e:
    self.logger.log_event("latency_measurement_failed", {
        "symbol": symbol,
        "error": str(e)
    })
"""