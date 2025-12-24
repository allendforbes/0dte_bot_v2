"""
Elite Entry Engine v5.0 — Pure Structure Entry (No Greek Gating)

Architecture:
  1. detect_regime() → identifies RECLAIM/TREND based on VWAP/price/structure only
  2. acceptance_ok() → gates entry to avoid early failed reclaims (structure-only)
  3. build_signal() → constructs signal with scoring (VWAP energy only, no Greeks)

Greeks/IV are POST-ENTRY observability only and never gate entries.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class EliteSignal:
    bias: str
    grade: str
    regime: str
    score: float
    trail_mult: float


@dataclass
class RegimeDetection:
    """
    Detected regime (not yet accepted for entry).
    """
    bias: str  # "CALL" or "PUT"
    regime: str  # "RECLAIM" or "TREND"
    confidence: float  # 0.0 to 1.0


class EliteEntryEngine:
    # ========================================================================
    # CONFIGURATION
    # ========================================================================
    
    # Acceptance criteria (structure-only)
    ACCEPTANCE_HOLD_BARS = 2  # Bars to hold above/below VWAP
    ACCEPTANCE_RANGE_BREAK = True  # Require range high/low break
    
    # Scoring (VWAP energy only, no Greeks)
    BASE_SCORE = 70.0
    BOOST_STRONG_SLOPE = 15.0  # abs(slope) > 0.05
    BOOST_HIGH_DEV = 10.0      # abs(dev) > 0.15
    
    # Grading
    GRADE_A_PLUS = 90.0
    TRAIL_A = 1.30
    TRAIL_A_PLUS = 1.40
    
    # ========================================================================
    # STAGE 1: REGIME DETECTION (STRUCTURE ONLY)
    # ========================================================================
    
    def detect_regime(self, snap: dict) -> Optional[RegimeDetection]:
        """
        BINARY regime detection: RECLAIM vs NOT.
        
        No time gates, no slope mins, no dev mins.
        Entry quality is handled by acceptance + trail, not detection.
        
        Returns:
            RegimeDetection if price/VWAP show directional alignment, None otherwise
        
        This is NOT a quality filter - it's a regime classifier.
        """
        
        # Extract structure-only fields
        dev = snap.get("vwap_dev")
        slope = snap.get("vwap_dev_change")
        
        # Basic validation only (missing data = can't classify)
        if dev is None or slope is None:
            return None
        
        # ----------------------------------------------------------------
        # BINARY RECLAIM DETECTION
        # ----------------------------------------------------------------
        # Price above VWAP AND rising = CALL reclaim
        if dev > 0 and slope > 0:
            return RegimeDetection(
                bias="CALL",
                regime="RECLAIM",
                confidence=0.5  # Fixed confidence, no gating
            )
        
        # Price below VWAP AND falling = PUT reclaim
        if dev < 0 and slope < 0:
            return RegimeDetection(
                bias="PUT",
                regime="RECLAIM",
                confidence=0.5  # Fixed confidence, no gating
            )
        
        # No directional alignment = no regime
        return None
    
    # ========================================================================
    # STAGE 2: ACCEPTANCE GATE (STRUCTURE ONLY)
    # ========================================================================
    
    def acceptance_ok(self, snap: dict, state: dict) -> bool:
        """
        Acceptance gate to avoid early failed reclaims.
        
        Args:
            snap: Current market snapshot (structure-only)
            state: Orchestrator tracking state with:
                - hold_bars: Number of bars held above/below VWAP
                - range_high: Local high since detection
                - range_low: Local low since detection
                - bias: CALL or PUT
        
        Returns:
            True if acceptance criteria met, False to wait
        
        Criteria:
            - Hold above/below VWAP for N bars
            - OR break range high/low (shows momentum follow-through)
        
        No Greeks, no IV, no chain quality.
        """
        
        price = snap.get("price")
        vwap = snap.get("vwap")
        
        if price is None or vwap is None:
            return False
        
        bias = state.get("bias")
        hold_bars = state.get("hold_bars", 0)
        range_high = state.get("range_high")
        range_low = state.get("range_low")
        
        if not bias:
            return False
        
        # ----------------------------------------------------------------
        # ACCEPTANCE CRITERION 1: Hold Bars
        # ----------------------------------------------------------------
        if hold_bars >= self.ACCEPTANCE_HOLD_BARS:
            return True
        
        # ----------------------------------------------------------------
        # ACCEPTANCE CRITERION 2: Range Break (momentum follow-through)
        # ----------------------------------------------------------------
        if self.ACCEPTANCE_RANGE_BREAK and range_high and range_low:
            if bias == "CALL" and price > range_high:
                return True
            elif bias == "PUT" and price < range_low:
                return True
        
        # Not yet accepted
        return False
    
    # ========================================================================
    # STAGE 3: SIGNAL CONSTRUCTION (VWAP ENERGY ONLY)
    # ========================================================================
    
    def build_signal(self, regime: RegimeDetection, snap: dict) -> EliteSignal:
        """
        Construct EliteSignal from accepted regime.
        
        Scoring uses VWAP energy only (no Greeks/IV).
        
        Args:
            regime: Detected regime from detect_regime()
            snap: Current market snapshot
        
        Returns:
            EliteSignal ready for entry
        """
        
        dev = snap.get("vwap_dev", 0.0)
        slope = snap.get("vwap_dev_change", 0.0)
        
        # Start with base score
        score = self.BASE_SCORE
        
        # ----------------------------------------------------------------
        # VWAP ENERGY BOOSTS (structure-only, no double-counting)
        # ----------------------------------------------------------------
        
        # Strong slope = strong momentum
        if abs(slope) > 0.05:
            score += self.BOOST_STRONG_SLOPE
        
        # High deviation = strong displacement
        if abs(dev) > 0.15:
            score += self.BOOST_HIGH_DEV
        
        # Note: regime.confidence is fixed at 0.5 (no dev-based gating)
        # No need to add it to score (would double-count dev)
        
        # ----------------------------------------------------------------
        # GRADING (score-based)
        # ----------------------------------------------------------------
        if score >= self.GRADE_A_PLUS:
            grade = "A+"
            trail_mult = self.TRAIL_A_PLUS
        else:
            grade = "A"
            trail_mult = self.TRAIL_A
        
        # ----------------------------------------------------------------
        # CONSTRUCT SIGNAL
        # ----------------------------------------------------------------
        return EliteSignal(
            bias=regime.bias,
            grade=grade,
            regime=regime.regime,
            score=float(score),
            trail_mult=float(trail_mult),
        )
    
    # ========================================================================
    # BACKWARD COMPATIBILITY: qualify() WRAPPER
    # ========================================================================
    
    def qualify(self, snap: dict, state: Optional[dict] = None) -> Optional[EliteSignal]:
        """
        ⚠️ DEPRECATED: Do not use qualify() in production entry path.
        
        Orchestrator should use 3-stage API directly:
          1. detect_regime(snap)
          2. acceptance_ok(snap, state)
          3. build_signal(regime, snap)
        
        This wrapper is for legacy compatibility only.
        
        Args:
            snap: Market snapshot (structure-only fields)
            state: Optional acceptance state (if None, accepts immediately)
        
        Returns:
            EliteSignal if entry ready, None otherwise
        """
        
        # Stage 1: Detect regime
        regime = self.detect_regime(snap)
        if not regime:
            return None
        
        # Stage 2: Check acceptance (if state provided)
        if state is not None:
            if not self.acceptance_ok(snap, state):
                return None  # Regime detected but not yet accepted
        
        # Stage 3: Build signal
        return self.build_signal(regime, snap)