"""
Elite Entry Engine v3.0 — Trend + Reclaim (Convexity Purified)
All optional operands guarded explicitly (Pyright friendly).
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

    # Trend thresholds
    SLOPE_TREND_UP = 0.02
    SLOPE_TREND_DN = -0.02

    # Absolute minimum momentum to qualify
    SLOPE_MIN = 0.005
    DEV_MIN = 0.05

    # Microstructure thresholds
    UPVOL_MIN = 65
    FLOW_CALL = 1.20
    FLOW_PUT = 0.80

    # Base scoring
    BASE_TREND = 70
    RECLAIM_SCORE = 70

    # Boosts
    BOOST_MICRO = 10
    BOOST_IV = 10
    BOOST_STRONG_SLOPE = 10

    # Grade thresholds
    GRADE_A_PLUS = 85

    # Trail multipliers
    TRAIL_A = 1.30
    TRAIL_A_PLUS = 1.40

    # ================================================================
    def qualify(self, snap: dict) -> Optional[EliteSignal]:
        # Extract required fields
        symbol = snap.get("symbol")
        price = snap.get("price")
        vwap = snap.get("vwap")
        dev = snap.get("vwap_dev")
        slope = snap.get("vwap_dev_change")
        secs = snap.get("seconds_since_open", 0)

        # Optional microstructure
        upvol = snap.get("upvol_pct")
        flow = snap.get("flow_ratio")
        ivc = snap.get("iv_change")
        skew = snap.get("skew_shift")

        # -----------------------------
        # Hard validation
        # -----------------------------
        if (
            symbol is None
            or price is None
            or vwap is None
            or dev is None
            or slope is None
        ):
            return None

        if secs > self.MORNING_LIMIT_SEC:
            return None

        # Absolute minimum momentum
        if abs(slope) < self.SLOPE_MIN:
            return None
        if abs(dev) < self.DEV_MIN:
            return None

        # -----------------------------
        # RECLAIM PRECEDENCE
        # -----------------------------
        bias: Optional[str] = None
        score = float(self.BASE_TREND)
        regime = "TREND_EARLY"

        if dev > 0.10 and slope > 0:
            bias = "CALL"
            score = float(self.RECLAIM_SCORE)
            regime = "RECLAIM"

        elif dev < -0.10 and slope < 0:
            bias = "PUT"
            score = float(self.RECLAIM_SCORE)
            regime = "RECLAIM"

        else:
            # -----------------------------
            # TREND LOGIC
            # -----------------------------
            if price < vwap and slope > 0:
                bias = "CALL"
            elif price > vwap and slope < 0:
                bias = "PUT"
            else:
                return None

        if bias is None:
            return None

        # -----------------------------
        # MICROSTRUCTURE BOOSTS
        # (inline non-None checks so Pyright narrows types)
        # -----------------------------
        if bias == "CALL":
            if (
                upvol is not None and flow is not None and ivc is not None and skew is not None
                and upvol >= self.UPVOL_MIN
                and flow >= self.FLOW_CALL
                and ivc >= 0
                and skew >= 0
            ):
                score += self.BOOST_MICRO
        else:  # PUT
            if (
                upvol is not None and flow is not None and ivc is not None and skew is not None
                and upvol >= self.UPVOL_MIN
                and flow <= self.FLOW_PUT
                and ivc >= 0
                and skew <= 0
            ):
                score += self.BOOST_MICRO

        # IV boost
        if ivc is not None and ivc > 0:
            score += self.BOOST_IV

        # Strong slope boost
        if abs(slope) > 0.05:
            score += self.BOOST_STRONG_SLOPE

        # -----------------------------
        # GRADING
        # -----------------------------
        if score >= self.GRADE_A_PLUS:
            grade = "A+"
            trail_mult = float(self.TRAIL_A_PLUS)
        else:
            grade = "A"
            trail_mult = float(self.TRAIL_A)

        # -----------------------------
        # FINISH
        # -----------------------------
        return EliteSignal(
            bias=bias,
            grade=grade,
            regime=regime,
            score=float(score),
            trail_mult=trail_mult,
        )