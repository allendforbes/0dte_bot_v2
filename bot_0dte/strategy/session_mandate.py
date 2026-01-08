"""
SessionMandate — Single Authority for Entry Permission

Architecture:
    SessionMandateEngine.determine() → SessionMandate
    
    SessionMandate.allows_entry() is the ONLY gate for entry logic.
    All other fields (confidence, reason, regime_type) are metadata only.

Invariants:
    1. allows_entry() returns True iff state == ENTRY_ALLOWED
    2. VWAP is context (populates reason/confidence), never affects state
    3. Early and late session use identical execution paths
    4. No execution branching on confidence or reason
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
import time


class RegimeState(Enum):
    """
    Binary permission states.
    
    ENTRY_ALLOWED: Permission granted, proceed to strike selection
    SUPPRESSED: Bias detected but acceptance criteria not met
    NO_TRADE: No bias detected, cooldown active, or system not ready
    """
    ENTRY_ALLOWED = "ENTRY_ALLOWED"
    SUPPRESSED = "SUPPRESSED"
    NO_TRADE = "NO_TRADE"


@dataclass
class SessionMandate:
    """
    Immutable permission object returned by SessionMandateEngine.
    
    The ONLY method that affects control flow is allows_entry().
    All other fields are observability metadata.
    """
    state: RegimeState
    bias: Optional[str]  # "CALL" or "PUT" or None
    regime_type: Optional[str]  # "TREND" or "RECLAIM" (classification for logging)
    confidence: float  # 0.0 to 1.0 (metadata only)
    reason: str  # Explains state (metadata only)
    reference_price: Optional[float] = None  # Reference used for bias (for auditability)
    
    def allows_entry(self) -> bool:
        """
        Single permission gate.
        
        Returns True if and only if state == ENTRY_ALLOWED.
        This is the ONLY method that should be used for control flow.
        """
        return self.state == RegimeState.ENTRY_ALLOWED
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "state": self.state.value,
            "bias": self.bias,
            "regime_type": self.regime_type,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "reference_price": self.reference_price,
            "allows_entry": self.allows_entry(),
        }


class SessionMandateEngine:
    """
    Centralized regime determination.
    
    Owns:
        - Bias detection (price location)
        - Acceptance tracking (hold_bars, range_break)
        - Cooldown enforcement
        - Regime classification (TREND vs RECLAIM, for metadata)
    
    Does NOT own:
        - Signal scoring (lives in entry engine)
        - Strike selection
        - Execution
    """
    
    # Acceptance criteria
    HOLD_BARS_REQUIRED = 2
    HOLD_INTERVAL_SEC = 5.0  # Seconds per "bar"
    
    # Cooldown
    POST_EXIT_COOLDOWN_SEC = 30.0
    
    def __init__(self):
        # Acceptance state per symbol
        self._acceptance: Dict[str, Dict[str, Any]] = {}
        
        # Last exit timestamp (for cooldown)
        self._last_exit_ts: Optional[float] = None
        
        # Reference prices per symbol (for bias when VWAP unavailable)
        self._reference_prices: Dict[str, Dict[str, float]] = {}
    
    def set_last_exit_ts(self, ts: float):
        """Called by orchestrator after exit."""
        self._last_exit_ts = ts
    
    def set_reference_price(self, symbol: str, price_type: str, value: float):
        """
        Set reference price for symbol.
        
        Args:
            symbol: Trading symbol
            price_type: One of "open", "onh", "onl", "vwap", "prev_close"
            value: Price value
        """
        if symbol not in self._reference_prices:
            self._reference_prices[symbol] = {}
        self._reference_prices[symbol][price_type] = value
    
    def get_reference_price(self, symbol: str, snap: Dict[str, Any], log_func=None) -> Optional[float]:
        """
        Get best available reference price for bias determination.
        
        Priority: VWAP → ONH → ONL → PREV_CLOSE → OPEN
        
        This fallback chain ensures the mandate can evaluate early in the session,
        even if VWAP hasn't printed yet.
        
        Args:
            symbol: Trading symbol
            snap: Market snapshot (may contain vwap, overnight_high, etc.)
            log_func: Optional logging function (callable that accepts string)
        
        Returns:
            Reference price or None
        """
        refs = self._reference_prices.get(symbol, {})
        
        def _log(msg: str):
            if log_func is not None:
                log_func(msg)
        
        # Build ordered fallback chain: (label, value)
        # Check snap first for live values, then stored refs
        price_sources = [
            ("VWAP", snap.get("vwap")),
            ("ONH", snap.get("overnight_high") or refs.get("onh")),
            ("ONL", snap.get("overnight_low") or refs.get("onl")),
            ("PREV_CLOSE", snap.get("previous_close") or refs.get("prev_close")),
            ("OPEN", snap.get("open_price") or refs.get("open")),
        ]
        
        for label, price in price_sources:
            if price is not None and price > 0:
                _log(f"[refprice] Using {label}: {price}")
                return price
        
        _log("[refprice] No valid reference price found")
        return None
    
    def _get_acceptance_state(self, symbol: str) -> Dict[str, Any]:
        """Get or create acceptance state for symbol."""
        if symbol not in self._acceptance:
            self._acceptance[symbol] = {
                "hold_bars": 0,
                "range_high": None,
                "range_low": None,
                "bias": None,
                "last_hold_ts": None,
            }
        return self._acceptance[symbol]
    
    def _reset_acceptance_state(self, symbol: str):
        """Reset acceptance state for symbol."""
        self._acceptance[symbol] = {
            "hold_bars": 0,
            "range_high": None,
            "range_low": None,
            "bias": None,
            "last_hold_ts": None,
        }
    def get_debug_state(self, symbol: str) -> Dict[str, Any]:
        """Return debug state for UI snapshot."""
        state = self._acceptance.get(symbol, {})
        cooldown_active = False
        if self._last_exit_ts:
            cooldown_active = (time.monotonic() - self._last_exit_ts) < self.POST_EXIT_COOLDOWN_SEC
        
        return {
            "acceptance": {
                "bias": state.get("bias"),
                "hold_bars": state.get("hold_bars", 0),
                "last_aligned": state.get("last_hold_ts") is not None,
            },
            "config": {
                "hold_bars_required": self.HOLD_BARS_REQUIRED,
            },
            "cooldown_active": cooldown_active,
        }
    def determine(
        self,
        symbol: str,
        snap: Dict[str, Any],
    ) -> SessionMandate:
        """
        Determine session mandate for symbol.
        
        This is the SINGLE AUTHORITY for entry permission.
        
        Args:
            symbol: Trading symbol
            snap: Market snapshot containing:
                - price: Current underlying price
                - vwap: Current VWAP (may be None early session)
                - vwap_dev: Price - VWAP
                - vwap_dev_change: Change in deviation
                - seconds_since_open: Time since market open
                - reference_price: Explicit reference (optional, from orchestrator)
        
        Returns:
            SessionMandate with permission state and metadata
        """
        
        price = snap.get("price")
        vwap = snap.get("vwap")
        vwap_dev = snap.get("vwap_dev")
        vwap_dev_change = snap.get("vwap_dev_change")
        seconds_since_open = snap.get("seconds_since_open", 0.0)
        
        now = time.monotonic()
        
        # ================================================================
        # CHECK 1: COOLDOWN
        # ================================================================
        if self._last_exit_ts is not None:
            if now - self._last_exit_ts < self.POST_EXIT_COOLDOWN_SEC:
                return SessionMandate(
                    state=RegimeState.NO_TRADE,
                    bias=None,
                    regime_type=None,
                    confidence=0.0,
                    reason="post_exit_cooldown",
                    reference_price=None,
                )
        
        # ================================================================
        # CHECK 2: DATA VALIDITY
        # ================================================================
        if price is None:
            return SessionMandate(
                state=RegimeState.NO_TRADE,
                bias=None,
                regime_type=None,
                confidence=0.0,
                reason="no_price_data",
                reference_price=None,
            )
        
        # ================================================================
        # STEP 1: REFERENCE PRICE RESOLUTION
        # ================================================================
        # Use explicit reference from snap if provided, otherwise resolve
        reference_price = snap.get("reference_price")
        if reference_price is None:
            reference_price = self.get_reference_price(symbol, snap)
        
        # Track whether VWAP is available (for metadata)
        vwap_available = (vwap is not None)
        
        # ================================================================
        # STEP 2: BIAS DETECTION (location-based)
        # ================================================================
        if reference_price is None:
            # No reference available → cannot determine bias
            # This is NO_TRADE, not because VWAP is missing, but because
            # we have no reference at all (no VWAP, no ONH/ONL, no open)
            return SessionMandate(
                state=RegimeState.NO_TRADE,
                bias=None,
                regime_type=None,
                confidence=0.0,
                reason="no_reference_price",
                reference_price=None,
            )
        
        # Calculate deviation from reference
        dev = price - reference_price
        
        # Determine bias from location relative to reference
        if dev > 0:
            bias = "CALL"
        elif dev < 0:
            bias = "PUT"
        else:
            # Pinned exactly at reference → no directional bias
            return SessionMandate(
                state=RegimeState.NO_TRADE,
                bias=None,
                regime_type=None,
                confidence=0.0,
                reason="pinned_at_reference",
                reference_price=reference_price,
            )
        
        # ================================================================
        # STEP 3: REGIME CLASSIFICATION (metadata only, does not gate)
        # ================================================================
        # RECLAIM: slope aligned with deviation (momentum confirmation)
        # TREND: location only (no slope requirement)
        # Note: slope requires VWAP to be meaningful
        
        if vwap_available and vwap_dev_change is not None:
            slope_aligned = (
                (bias == "CALL" and vwap_dev_change > 0) or
                (bias == "PUT" and vwap_dev_change < 0)
            )
            regime_type = "RECLAIM" if slope_aligned else "TREND"
        else:
            # No VWAP → default to TREND (location-only)
            regime_type = "TREND"
        
        # ================================================================
        # STEP 4: ACCEPTANCE STATE MANAGEMENT
        # ================================================================
        state = self._get_acceptance_state(symbol)
        
        # Bias flip → reset acceptance
        if state["bias"] != bias:
            self._reset_acceptance_state(symbol)
            state = self._get_acceptance_state(symbol)
            state["bias"] = bias
            state["range_high"] = price
            state["range_low"] = price
            state["last_hold_ts"] = now
        
        # ================================================================
        # STEP 5: ACCEPTANCE CRITERIA CHECK (before updating range)
        # ================================================================
        acceptance_met = False
        acceptance_reason = ""
        
        # Criterion 2: Range break (checked FIRST, before range update)
        if bias == "CALL" and state["range_high"] is not None:
            if price > state["range_high"]:
                acceptance_met = True
                acceptance_reason = "range_break_high"
        elif bias == "PUT" and state["range_low"] is not None:
            if price < state["range_low"]:
                acceptance_met = True
                acceptance_reason = "range_break_low"
        
        # Update range tracking (AFTER range break check)
        if state["range_high"] is None or price > state["range_high"]:
            state["range_high"] = price
        if state["range_low"] is None or price < state["range_low"]:
            state["range_low"] = price
        
        # Check alignment for hold bar accumulation
        # Use VWAP if available, otherwise reference price
        alignment_ref = vwap if vwap_available else reference_price
        aligned = (
            (bias == "CALL" and price > alignment_ref) or
            (bias == "PUT" and price < alignment_ref)
        )
        
        if aligned:
            # Accumulate hold bars
            if state["last_hold_ts"] is None:
                state["last_hold_ts"] = now
            elif now - state["last_hold_ts"] >= self.HOLD_INTERVAL_SEC:
                state["hold_bars"] += 1
                state["last_hold_ts"] = now
        else:
            # Reset hold bars on alignment violation
            state["hold_bars"] = 0
            state["last_hold_ts"] = None
        
        # Criterion 1: Hold bars (from accumulation above)
        if not acceptance_met and state["hold_bars"] >= self.HOLD_BARS_REQUIRED:
            acceptance_met = True
            acceptance_reason = "hold_bars"
        
        # ================================================================
        # STEP 6: BUILD CONFIDENCE (metadata only)
        # ================================================================
        # Base confidence
        confidence = 0.5
        
        # Early session modifier (metadata, not gating)
        if seconds_since_open < 300:
            confidence = 0.3
        
        # VWAP unavailable modifier (metadata, not gating)
        if not vwap_available:
            confidence -= 0.1
        
        # Slope alignment boost (only if VWAP available)
        if vwap_available and vwap_dev_change is not None:
            slope_aligned = (
                (bias == "CALL" and vwap_dev_change > 0) or
                (bias == "PUT" and vwap_dev_change < 0)
            )
            if slope_aligned:
                confidence += 0.2
        
        # Strong deviation boost
        if abs(dev) > 0.15:
            confidence += 0.1
        
        confidence = max(0.0, min(confidence, 1.0))
        
        # ================================================================
        # STEP 7: BUILD REASON STRING (metadata only)
        # ================================================================
        reason_parts = []
        
        if seconds_since_open < 300:
            reason_parts.append("early_session")
        else:
            reason_parts.append("normal_session")
        
        if not vwap_available:
            reason_parts.append("vwap_unavailable")
        
        if acceptance_met:
            reason_parts.append(f"accepted:{acceptance_reason}")
        else:
            reason_parts.append(f"pending:hold_bars={state['hold_bars']}")
        
        reason = "|".join(reason_parts)
        
        # ================================================================
        # STEP 8: RETURN MANDATE
        # ================================================================
        if acceptance_met:
            return SessionMandate(
                state=RegimeState.ENTRY_ALLOWED,
                bias=bias,
                regime_type=regime_type,
                confidence=confidence,
                reason=reason,
                reference_price=reference_price,
            )
        else:
            return SessionMandate(
                state=RegimeState.SUPPRESSED,
                bias=bias,
                regime_type=regime_type,
                confidence=confidence,
                reason=reason,
                reference_price=reference_price,
            )