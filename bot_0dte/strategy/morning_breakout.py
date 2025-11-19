"""
MorningBreakout — Hybrid Early Entry (Mode C)
Strict, convex-entry signal engine.

Early Mode (pre-reclaim / pre-reject):
    • price still on wrong side of VWAP
    • vwap_dev_change in breakout direction
    • upvol_pct >= 60
    • flow imbalance (CALL ≥1.20, PUT ≤0.80)
    • vol surface can veto or upgrade
    • aggressive RR: tp=5R, sl=0.35R, trail=1.3

Standard Mode (fallback):
    • VWAP reclaim or rejection confirmed
    • moderate volume & flow
    • normal RR: tp=3R, sl=0.5R, trail=1.2
"""

import datetime as dt


class MorningBreakout:
    def __init__(self, telemetry=None):
        self.telemetry = telemetry

        # microstructure thresholds
        self.MIN_UPVOL = 60
        self.MIN_FLOW_CALL = 1.20
        self.MAX_FLOW_PUT = 0.80

        # slope thresholds to avoid random noise
        self.MIN_SLOPE_UP = 0.00
        self.MIN_SLOPE_DN = 0.00

        # morning-only
        self.MORNING_LIMIT_SEC = 5400  # first 90 minutes

    # ---------------------------------------------------------
    def _is_morning(self, secs):
        return secs <= self.MORNING_LIMIT_SEC

    # ---------------------------------------------------------
    def _vol_support(self, iv_chg, skew):
        return (
            iv_chg is not None and skew is not None and
            iv_chg > 0 and skew > 0
        )

    # ---------------------------------------------------------
    def _vol_against(self, iv_chg, skew):
        return (
            iv_chg is not None and skew is not None and
            iv_chg < 0 and skew < 0
        )

    # =========================================================
    # MAIN QUALIFIER
    # =========================================================
    def qualify(self, snap: dict):
        """
        snap = {
            "symbol": ...,
            "price": ...,
            "vwap": ...,
            "vwap_dev": price - vwap,
            "vwap_dev_change": ...,
            "upvol_pct": ...,
            "flow_ratio": ...,
            "iv_change": ...,
            "skew_shift": ...,
            "seconds_since_open": ...
        }
        """

        price = snap["price"]
        vwap = snap.get("vwap")
        secs = snap.get("seconds_since_open", 0)

        if price is None or vwap is None:
            return None
        if not self._is_morning(secs):
            return None

        dev = snap["vwap_dev"]
        slope = snap.get("vwap_dev_change", 0)
        upvol = snap.get("upvol_pct", None)
        flow = snap.get("flow_ratio", None)
        ivc = snap.get("iv_change", None)
        skew = snap.get("skew_shift", None)

        # =====================================================
        # LAYER 1 — STRUCTURE (pre-reclaim / pre-reject)
        # =====================================================
        pre_call = price < vwap and slope > self.MIN_SLOPE_UP
        pre_put = price > vwap and slope < self.MIN_SLOPE_DN

        # =====================================================
        # LAYER 2 — MICROSTRUCTURE (must pass both)
        # =====================================================
        micro_call = (
            upvol is not None and flow is not None and
            upvol >= self.MIN_UPVOL and flow >= self.MIN_FLOW_CALL
        )
        micro_put = (
            upvol is not None and flow is not None and
            upvol >= self.MIN_UPVOL and flow <= self.MAX_FLOW_PUT
        )

        # =====================================================
        # LAYER 3 — VOL SURFACE (booster OR veto)
        # =====================================================
        vol_support = self._vol_support(ivc, skew)
        vol_against = self._vol_against(ivc, skew)

        # =====================================================
        # EARLY ENTRY — CALL
        # =====================================================
        if pre_call and micro_call:
            if not vol_against:
                return {
                    "bias": "CALL",
                    "regime": "TREND_EARLY",
                    "grade": "A+ Early" if vol_support else "A Early",
                    "vol_path": "SUPPORT" if vol_support else "NEUTRAL",
                    "tp_mult": 5.0,
                    "sl_mult": 0.35,
                    "trail_mult": 1.3,
                }
            # if vol_against → fall through to standard mode

        # =====================================================
        # EARLY ENTRY — PUT
        # =====================================================
        if pre_put and micro_put:
            if not vol_against:
                return {
                    "bias": "PUT",
                    "regime": "TREND_EARLY",
                    "grade": "A+ Early" if vol_support else "A Early",
                    "vol_path": "SUPPORT" if vol_support else "NEUTRAL",
                    "tp_mult": 5.0,
                    "sl_mult": 0.35,
                    "trail_mult": 1.3,
                }
            # vol-against → fall through

        # =====================================================
        # STANDARD MODE (fallback)
        # Clean VWAP reclaim / rejection logic
        # =====================================================
        reclaim = dev > 0 and slope > self.MIN_SLOPE_UP
        reject = dev < 0 and slope < self.MIN_SLOPE_DN

        # CALL reclaim
        if reclaim:
            return {
                "bias": "CALL",
                "regime": "RECLAIM",
                "grade": "A",
                "vol_path": "CONFIRM" if vol_support else "NEUTRAL",
                "tp_mult": 3.0,
                "sl_mult": 0.50,
                "trail_mult": 1.2,
            }

        # PUT reject
        if reject:
            return {
                "bias": "PUT",
                "regime": "REJECT",
                "grade": "A",
                "vol_path": "CONFIRM" if vol_support else "NEUTRAL",
                "tp_mult": 3.0,
                "sl_mult": 0.50,
                "trail_mult": 1.2,
            }

        return None
