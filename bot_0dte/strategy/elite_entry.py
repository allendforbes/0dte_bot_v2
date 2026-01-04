"""
Elite Entry Engine v6.0 — Pure Signal Builder

Architecture:
    build_signal(mandate, snap) → EliteSignal
    
    This engine is a PURE EXECUTOR. It does NOT:
    - Detect regime (moved to SessionMandateEngine)
    - Gate acceptance (moved to SessionMandateEngine)
    - Decide eligibility (mandate.allows_entry() already checked)
    
    It ONLY:
    - Constructs EliteSignal from an already-approved mandate
    - Scores based on VWAP energy (observability)
    - Sets trail multiplier based on regime classification

Invariants:
    1. build_signal() assumes permission already granted
    2. No regime detection logic
    3. No acceptance checking logic
    4. Pure input → output transformation
"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot_0dte.strategy.session_mandate import SessionMandate


@dataclass
class EliteSignal:
    """
    Entry signal produced by EliteEntryEngine.
    
    All fields are informational for execution and logging.
    Permission has already been granted by SessionMandate.
    """
    bias: str  # "CALL" or "PUT"
    grade: str  # "A+", "A", "B" (for logging/tier)
    regime: str  # "TREND" or "RECLAIM" (from mandate)
    score: float  # 0-100 (for logging)
    trail_mult: float  # Trail multiplier for risk management


class EliteEntryEngine:
    """
    Pure signal builder.
    
    Called ONLY after SessionMandate.allows_entry() returns True.
    Does not make eligibility decisions.
    """
    
    # ========================================================================
    # SCORING CONFIGURATION
    # ========================================================================
    
    # Base scores by regime type
    BASE_SCORE_TREND = 60.0
    BASE_SCORE_RECLAIM = 70.0
    
    # VWAP energy boosts (observability scoring)
    BOOST_STRONG_SLOPE = 15.0  # abs(slope) > 0.05
    BOOST_HIGH_DEV = 10.0      # abs(dev) > 0.15
    
    # Grading thresholds
    GRADE_A_PLUS_THRESHOLD = 90.0
    GRADE_A_THRESHOLD = 70.0
    
    # Trail multipliers
    TRAIL_TREND = 1.25
    TRAIL_RECLAIM_A = 1.30
    TRAIL_RECLAIM_A_PLUS = 1.40
    
    # ========================================================================
    # PUBLIC API
    # ========================================================================
    
    def build_signal(
        self,
        mandate: "SessionMandate",
        snap: dict,
    ) -> EliteSignal:
        """
        Construct EliteSignal from approved mandate.
        
        PRECONDITION: mandate.allows_entry() == True
        
        This method does NOT check permission. The caller (orchestrator)
        MUST verify mandate.allows_entry() before calling.
        
        Args:
            mandate: Approved SessionMandate with:
                - bias: "CALL" or "PUT"
                - regime_type: "TREND" or "RECLAIM"
                - confidence: 0.0 to 1.0 (not used for scoring)
            snap: Market snapshot with:
                - vwap_dev: Price deviation from VWAP
                - vwap_dev_change: Change in deviation (slope)
        
        Returns:
            EliteSignal ready for execution
        """
        
        # Extract fields from mandate
        bias = mandate.bias
        regime_type = mandate.regime_type or "TREND"
        
        # Extract VWAP energy from snap
        vwap_dev = snap.get("vwap_dev", 0.0) or 0.0
        vwap_dev_change = snap.get("vwap_dev_change", 0.0) or 0.0
        
        # ----------------------------------------------------------------
        # SCORING
        # ----------------------------------------------------------------
        if regime_type == "TREND":
            score = self.BASE_SCORE_TREND  # 60.0
        else:  # RECLAIM
            score = self.BASE_SCORE_RECLAIM  # 70.0
        
        # VWAP energy boosts
        if abs(vwap_dev_change) > 0.05:
            score += self.BOOST_STRONG_SLOPE  # +15
        
        if abs(vwap_dev) > 0.15:
            score += self.BOOST_HIGH_DEV  # +10
        
        # ----------------------------------------------------------------
        # GRADING + TRAIL MULTIPLIER
        # ----------------------------------------------------------------
        # Both TREND and RECLAIM can earn grades based on score
        # This is observability only - does not affect execution behavior
        
        if score >= self.GRADE_A_PLUS_THRESHOLD:  # 90+
            grade = "A+"
            trail_mult = self.TRAIL_RECLAIM_A_PLUS  # 1.40
        elif score >= self.GRADE_A_THRESHOLD:  # 70+
            grade = "A"
            trail_mult = self.TRAIL_RECLAIM_A  # 1.30
        else:
            grade = "B"
            trail_mult = self.TRAIL_TREND  # 1.25
        
        # ----------------------------------------------------------------
        # CONSTRUCT SIGNAL
        # ----------------------------------------------------------------
        return EliteSignal(
            bias=bias,
            grade=grade,
            regime=regime_type,
            score=float(score),
            trail_mult=float(trail_mult),
        )
    
    # ========================================================================
    # DEPRECATED METHODS (for reference during migration)
    # ========================================================================
    
    def detect_regime(self, snap: dict):
        """
        ⚠️ REMOVED: Regime detection moved to SessionMandateEngine.
        
        This method should not be called. Raises error to catch misuse.
        """
        raise NotImplementedError(
            "detect_regime() has been removed. "
            "Use SessionMandateEngine.determine() instead."
        )
    
    def acceptance_ok(self, snap: dict, state: dict) -> bool:
        """
        ⚠️ REMOVED: Acceptance checking moved to SessionMandateEngine.
        
        This method should not be called. Raises error to catch misuse.
        """
        raise NotImplementedError(
            "acceptance_ok() has been removed. "
            "Use SessionMandateEngine.determine() instead."
        )
    
    def qualify(self, snap: dict, state: Optional[dict] = None):
        """
        ⚠️ REMOVED: Combined detection+acceptance moved to SessionMandateEngine.
        
        This method should not be called. Raises error to catch misuse.
        """
        raise NotImplementedError(
            "qualify() has been removed. "
            "Use SessionMandateEngine.determine() + build_signal() instead."
        )