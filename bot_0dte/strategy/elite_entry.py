"""
Elite Entry Engine v6.1 — Pure Signal Builder (REFACTORED)

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

REFACTORED:
    - Enhanced scoring with multiple inputs
    - Better trail multiplier selection
    - Improved observability
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from bot_0dte.strategy.session_mandate import SessionMandate

logger = logging.getLogger(__name__)


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
    
    # Additional observability fields
    vwap_energy: float = 0.0  # Composite VWAP score
    confidence: float = 0.0  # From mandate
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "bias": self.bias,
            "grade": self.grade,
            "regime": self.regime,
            "score": round(self.score, 2),
            "trail_mult": round(self.trail_mult, 2),
            "vwap_energy": round(self.vwap_energy, 2),
            "confidence": round(self.confidence, 3),
        }


class EliteEntryEngine:
    """
    Pure signal builder (REFACTORED).
    
    Called ONLY after SessionMandate.allows_entry() returns True.
    Does not make eligibility decisions.
    
    ENHANCEMENTS:
        - Multi-factor scoring
        - Regime-aware trail multipliers
        - VWAP energy calculation
    """
    
    # ========================================================================
    # SCORING CONFIGURATION
    # ========================================================================
    
    # Base scores by regime type
    BASE_SCORE_TREND = 60.0
    BASE_SCORE_RECLAIM = 70.0
    
    # VWAP energy boosts
    BOOST_STRONG_SLOPE = 15.0  # abs(slope) > SLOPE_THRESHOLD
    BOOST_HIGH_DEV = 10.0      # abs(dev) > DEV_THRESHOLD
    BOOST_ALIGNED_MOMENTUM = 5.0  # Slope aligned with bias
    
    # Confidence boost
    BOOST_HIGH_CONFIDENCE = 10.0  # confidence > 0.7
    
    # Thresholds
    SLOPE_THRESHOLD = 0.05
    DEV_THRESHOLD = 0.15
    HIGH_CONFIDENCE_THRESHOLD = 0.70
    
    # Grading thresholds
    GRADE_A_PLUS_THRESHOLD = 90.0
    GRADE_A_THRESHOLD = 70.0
    
    # Trail multipliers
    TRAIL_TREND = 1.25
    TRAIL_RECLAIM_A = 1.30
    TRAIL_RECLAIM_A_PLUS = 1.40
    TRAIL_HIGH_CONFIDENCE = 1.50
    
    def __init__(self, log_func=None):
        """
        Initialize entry engine.
        
        Args:
            log_func: Optional logging function
        """
        self._log_func = log_func or (lambda msg: logger.debug(msg))
    
    def _log(self, msg: str):
        self._log_func(msg)
    
    def _compute_vwap_energy(
        self,
        bias: str,
        vwap_dev: Optional[float],
        vwap_dev_change: Optional[float],
    ) -> float:
        """
        Compute VWAP energy score (0-100 scale).
        
        Factors:
            - Deviation magnitude
            - Slope magnitude
            - Alignment with bias
        """
        energy = 0.0
        
        if vwap_dev is None:
            return energy
        
        # Deviation contribution (0-40)
        dev_abs = abs(vwap_dev)
        if dev_abs > self.DEV_THRESHOLD:
            energy += 40.0
        elif dev_abs > self.DEV_THRESHOLD / 2:
            energy += 20.0
        elif dev_abs > 0:
            energy += 10.0
        
        # Slope contribution (0-40)
        if vwap_dev_change is not None:
            slope_abs = abs(vwap_dev_change)
            if slope_abs > self.SLOPE_THRESHOLD:
                energy += 40.0
            elif slope_abs > self.SLOPE_THRESHOLD / 2:
                energy += 20.0
            elif slope_abs > 0:
                energy += 10.0
        
        # Alignment contribution (0-20)
        if vwap_dev_change is not None:
            slope_aligned = (
                (bias == "CALL" and vwap_dev_change > 0) or
                (bias == "PUT" and vwap_dev_change < 0)
            )
            if slope_aligned:
                energy += 20.0
        
        return energy
    
    def build_signal(
        self,
        mandate: "SessionMandate",
        snap: Dict[str, Any],
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
                - confidence: 0.0 to 1.0
            snap: Market snapshot with:
                - vwap_dev: Price deviation from VWAP
                - vwap_dev_change: Change in deviation (slope)
        
        Returns:
            EliteSignal ready for execution
        """
        
        # Extract fields from mandate
        bias = mandate.bias
        regime_type = mandate.regime_type or "TREND"
        confidence = mandate.confidence
        
        # Extract VWAP data from snap
        vwap_dev = snap.get("vwap_dev")
        vwap_dev_change = snap.get("vwap_dev_change")
        
        # ----------------------------------------------------------------
        # VWAP ENERGY
        # ----------------------------------------------------------------
        vwap_energy = self._compute_vwap_energy(bias, vwap_dev, vwap_dev_change)
        
        # ----------------------------------------------------------------
        # SCORING
        # ----------------------------------------------------------------
        if regime_type == "TREND":
            score = self.BASE_SCORE_TREND  # 60.0
        else:  # RECLAIM
            score = self.BASE_SCORE_RECLAIM  # 70.0
        
        # VWAP energy boosts
        if vwap_dev_change is not None and abs(vwap_dev_change) > self.SLOPE_THRESHOLD:
            score += self.BOOST_STRONG_SLOPE  # +15
        
        if vwap_dev is not None and abs(vwap_dev) > self.DEV_THRESHOLD:
            score += self.BOOST_HIGH_DEV  # +10
        
        # Alignment boost
        if vwap_dev_change is not None:
            slope_aligned = (
                (bias == "CALL" and vwap_dev_change > 0) or
                (bias == "PUT" and vwap_dev_change < 0)
            )
            if slope_aligned:
                score += self.BOOST_ALIGNED_MOMENTUM  # +5
        
        # Confidence boost
        if confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
            score += self.BOOST_HIGH_CONFIDENCE  # +10
        
        # ----------------------------------------------------------------
        # GRADING + TRAIL MULTIPLIER
        # ----------------------------------------------------------------
        if score >= self.GRADE_A_PLUS_THRESHOLD:  # 90+
            grade = "A+"
            trail_mult = self.TRAIL_RECLAIM_A_PLUS  # 1.40
        elif score >= self.GRADE_A_THRESHOLD:  # 70+
            grade = "A"
            trail_mult = self.TRAIL_RECLAIM_A  # 1.30
        else:
            grade = "B"
            trail_mult = self.TRAIL_TREND  # 1.25
        
        # High confidence override for trail
        if confidence >= 0.80:
            trail_mult = max(trail_mult, self.TRAIL_HIGH_CONFIDENCE)  # 1.50
        
        # ----------------------------------------------------------------
        # LOGGING
        # ----------------------------------------------------------------
        self._log(
            f"[SIGNAL] bias={bias} regime={regime_type} score={score:.1f} "
            f"grade={grade} trail={trail_mult:.2f} energy={vwap_energy:.1f}"
        )
        
        # ----------------------------------------------------------------
        # CONSTRUCT SIGNAL
        # ----------------------------------------------------------------
        return EliteSignal(
            bias=bias,
            grade=grade,
            regime=regime_type,
            score=float(score),
            trail_mult=float(trail_mult),
            vwap_energy=float(vwap_energy),
            confidence=float(confidence),
        )
    
    # ========================================================================
    # DEPRECATED METHODS (for reference during migration)
    # ========================================================================
    
    def detect_regime(self, snap: dict):
        """
        ⚠️ REMOVED: Regime detection moved to SessionMandateEngine.
        """
        raise NotImplementedError(
            "detect_regime() has been removed. "
            "Use SessionMandateEngine.determine() instead."
        )
    
    def acceptance_ok(self, snap: dict, state: dict) -> bool:
        """
        ⚠️ REMOVED: Acceptance checking moved to SessionMandateEngine.
        """
        raise NotImplementedError(
            "acceptance_ok() has been removed. "
            "Use SessionMandateEngine.determine() instead."
        )
    
    def qualify(self, snap: dict, state: Optional[dict] = None):
        """
        ⚠️ REMOVED: Combined detection+acceptance moved to SessionMandateEngine.
        """
        raise NotImplementedError(
            "qualify() has been removed. "
            "Use SessionMandateEngine.determine() + build_signal() instead."
        )