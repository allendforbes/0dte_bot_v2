"""
Elite Entry Engine (Diagnostic Edition)
---------------------------------------
Purpose:
    - Validate end-to-end bot pipeline live (today)
    - Relax thresholds so signals fire in mid-day / holiday chop
    - Remove time gating
    - Microstructure optional
    - Provide explicit debug reasons for rejects

This version is NOT meant for production trading.
It is meant to prove the entire system behaves correctly.
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
    # Relaxed thresholds for testing
    SLOPE_MIN = 0.0005        # was 0.005
    DEV_MIN   = 0.01          # was 0.05

    # TREND / RECLAIM thresholds
    SLOPE_RECLAIM = 0.0
    DEV_RECLAIM = 0.10

    # Base scoring
    BASE_TREND = 50
    BASE_RECLAIM = 70

    # Scoring boosts
    BOOST_STRONG_SLOPE = 10

    # Grade thresholds
    GRADE_A_PLUS = 85

    # Trail multipliers
    TRAIL_A = 1.25
    TRAIL_A_PLUS = 1.35

    # ================================================================
    def qualify(self, snap: dict) -> Optional[EliteSignal]:
        price = snap.get("price")
        vwap = snap.get("vwap")
        dev = snap.get("vwap_dev")
        slope = snap.get("vwap_dev_change")
        symbol = snap.get("symbol")

        # Optional microstructure (ignored unless present)
        upvol = snap.get("upvol_pct")
        flow  = snap.get("flow_ratio")
        ivc   = snap.get("iv_change")
        skew  = snap.get("skew_shift")

        # -----------------------------
        # Input validation
        # -----------------------------
        if price is None or vwap is None or dev is None or slope is None:
            return None

        # -----------------------------
        # Minimum movement (relaxed)
        # -----------------------------
        if abs(slope) < self.SLOPE_MIN:
            return None

        if abs(dev) < self.DEV_MIN:
            return None

        # -----------------------------
        # RECLAIM (dominant pattern)
        # -----------------------------
        bias = None
        score = float(self.BASE_TREND)
        regime = "TREND"

        # CALL reclaim
        if dev > self.DEV_RECLAIM and slope > self.SLOPE_RECLAIM:
            bias = "CALL"
            score = float(self.BASE_RECLAIM)
            regime = "RECLAIM"

        # PUT reclaim
        elif dev < -self.DEV_RECLAIM and slope < -self.SLOPE_RECLAIM:
            bias = "PUT"
            score = float(self.BASE_RECLAIM)
            regime = "RECLAIM"

        # -----------------------------
        # TREND logic (fallback)
        # -----------------------------
        else:
            if price > vwap and slope > 0:
                bias = "CALL"
                regime = "TREND_UP"

            elif price < vwap and slope < 0:
                bias = "PUT"
                regime = "TREND_DN"

            else:
                return None   # no alignment

        if bias is None:
            return None

        # -----------------------------
        # Scoring
        # -----------------------------
        if abs(slope) > 0.02:
            score += self.BOOST_STRONG_SLOPE

        # -----------------------------
        # Grade & trail
        # -----------------------------
        if score >= self.GRADE_A_PLUS:
            grade = "A+"
            trail_mult = float(self.TRAIL_A_PLUS)
        else:
            grade = "A"
            trail_mult = float(self.TRAIL_A)

        return EliteSignal(
            bias=bias,
            grade=grade,
            regime=regime,
            score=score,
            trail_mult=trail_mult,
        )
