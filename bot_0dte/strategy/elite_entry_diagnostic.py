"""
Elite Entry Engine (Diagnostic Edition + Phase-1 Early Entry Filters)
--------------------------------------------------------------------
Adds controlled early-entry logic:

    • upvol_pct > 55%
    • slope acceleration (slope_now > slope_prev)
    • gamma >= 0.0025
    • premium_ok flag required for TREND_UP / TREND_DN
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
    # Relaxed VWAP thresholds
    SLOPE_MIN = 0.0005
    DEV_MIN   = 0.01

    # RECLAIM thresholds
    SLOPE_RECLAIM = 0.0
    DEV_RECLAIM = 0.10

    # New Phase-1 filters
    UPVOL_MIN = 55
    GAMMA_MIN = 0.0025
    REQUIRE_PREMIUM_OK = True

    # Scoring
    BASE_TREND = 50
    BASE_RECLAIM = 70
    BOOST_STRONG_SLOPE = 10

    GRADE_A_PLUS = 85
    TRAIL_A = 1.25
    TRAIL_A_PLUS = 1.35

    # ================================================================
    def qualify(self, snap: dict) -> Optional[EliteSignal]:
        price = snap.get("price")
        vwap = snap.get("vwap")
        dev = snap.get("vwap_dev")
        slope = snap.get("vwap_dev_change")
        symbol = snap.get("symbol")

        # NEW (optional)
        upvol = snap.get("upvol_pct")
        gamma = snap.get("gamma")
        slope_prev = snap.get("slope_prev")  # orchestrator provides this
        premium_ok = snap.get("premium_ok", False)

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
        # RECLAIM (priority signal)
        # -----------------------------
        bias = None
        score = float(self.BASE_TREND)
        regime = "TREND"

        if dev > self.DEV_RECLAIM and slope > self.SLOPE_RECLAIM:
            bias = "CALL"
            score = float(self.BASE_RECLAIM)
            regime = "RECLAIM"

        elif dev < -self.DEV_RECLAIM and slope < -self.SLOPE_RECLAIM:
            bias = "PUT"
            score = float(self.BASE_RECLAIM)
            regime = "RECLAIM"

        # -----------------------------
        # TREND logic (EARLY ENTRY path)
        # -----------------------------
        else:
            # CALL early trend
            if price > vwap and slope > 0:
                bias = "CALL"
                regime = "TREND_UP"

            # PUT early trend
            elif price < vwap and slope < 0:
                bias = "PUT"
                regime = "TREND_DN"

            else:
                return None

            # ============================================================
            # Phase-1 Early Entry Filters (APPLY ONLY TO TREND signals)
            # ============================================================

            # 1) Upvolume confirmation
            if upvol is None or upvol < self.UPVOL_MIN:
                return None

            # 2) Slope acceleration — must be increasing
            if slope_prev is not None and slope <= slope_prev:
                return None

            # 3) Gamma convexity requirement
            if gamma is None or gamma < self.GAMMA_MIN:
                return None

            # 4) Premium band check (StrikeSelector sets premium_ok)
            if self.REQUIRE_PREMIUM_OK and not premium_ok:
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
