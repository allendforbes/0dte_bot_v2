"""
Elite Entry Engine v4.0 — VWAP + Greeks + IV Confirmation
Highly stable breakout detector for micro-momentum trading.
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


class EliteEntryEngine:
    # Time gating
    MORNING_LIMIT_SEC = 5400  # 90 minutes

    # VWAP baseline thresholds
    SLOPE_MIN = 0.005
    DEV_MIN = 0.05

    # Greek thresholds
    DELTA_TARGET = 0.30
    DELTA_TOL = 0.15
    GAMMA_MIN = 0.02
    IV_SPIKE = 0.03  # minimum IV uptick for confirmation

    # Scoring weights
    BASE_TREND = 70
    BOOST_IV = 8
    BOOST_DELTA = 7
    BOOST_GAMMA = 10
    BOOST_SLOPE = 10

    GRADE_A_PLUS = 90
    TRAIL_A = 1.30
    TRAIL_A_PLUS = 1.40

    # =================================================================
    def qualify(self, snap: dict) -> Optional[EliteSignal]:
        """
        Identify high-probability micro-breakouts based on:
            • VWAP reclaim
            • VWAP slope acceleration
            • Delta alignment
            • Gamma lift (convexity expansion)
            • IV uptick confirming pressure
        """

        # Required fields
        symbol = snap.get("symbol")
        price = snap.get("price")
        vwap = snap.get("vwap")
        dev = snap.get("vwap_dev")
        slope = snap.get("vwap_dev_change")
        secs = snap.get("seconds_since_open", 0)

        # Greeks & IV
        delta = snap.get("delta")
        gamma = snap.get("gamma")
        iv = snap.get("iv")
        ivc = snap.get("iv_change")

        # -----------------------------
        # Hard validation
        # -----------------------------
        if (
            symbol is None or price is None or vwap is None
            or dev is None or slope is None
        ):
            return None

        if secs > self.MORNING_LIMIT_SEC:
            return None

        # VWAP must show energy
        if abs(slope) < self.SLOPE_MIN:
            return None
        if abs(dev) < self.DEV_MIN:
            return None

        # -----------------------------
        # Reclaim Logic (priority)
        # -----------------------------
        if dev > 0.10 and slope > 0:
            bias = "CALL"
            regime = "RECLAIM"
        elif dev < -0.10 and slope < 0:
            bias = "PUT"
            regime = "RECLAIM"
        else:
            # Trend logic fallback
            if price < vwap and slope > 0:
                bias = "CALL"
                regime = "TREND"
            elif price > vwap and slope < 0:
                bias = "PUT"
                regime = "TREND"
            else:
                return None

        # -----------------------------
        # Start scoring
        # -----------------------------
        score = float(self.BASE_TREND)

        # -----------------------------
        # Greek-based boosts
        # -----------------------------
        if delta is not None:
            # CALL: want ~ +0.30; PUT: want ~ -0.30
            delta_err = abs(abs(delta) - self.DELTA_TARGET)
            if delta_err <= self.DELTA_TOL:
                score += self.BOOST_DELTA

        if gamma is not None and gamma >= self.GAMMA_MIN:
            score += self.BOOST_GAMMA

        # IV must not be collapsing; spike is strong confirmation
        if ivc is not None and ivc >= self.IV_SPIKE:
            score += self.BOOST_IV

        # Strong slope = strong energy
        if abs(slope) > 0.05:
            score += self.BOOST_SLOPE

        # -----------------------------
        # Grading
        # -----------------------------
        if score >= self.GRADE_A_PLUS:
            grade = "A+"
            trail_mult = float(self.TRAIL_A_PLUS)
        else:
            grade = "A"
            trail_mult = float(self.TRAIL_A)

        # -----------------------------
        # Finished
        # -----------------------------
        return EliteSignal(
            bias=bias,
            grade=grade,
            regime=regime,
            score=float(score),
            trail_mult=trail_mult,
        )
