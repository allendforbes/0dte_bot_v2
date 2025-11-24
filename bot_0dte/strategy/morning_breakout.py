"""
MorningBreakout — Hybrid Early Entry Strategy

WS-Native Compatible:
    • Works with enriched snapshot from Orchestrator
    • Defaults for unavailable microstructure fields
    • VWAP enrichment handled by orchestrator
    • No direct data fetching

Early Mode (pre-reclaim / pre-reject):
    • price still on wrong side of VWAP
    • vwap_dev_change in breakout direction
    • upvol_pct >= 60 (optional, defaults pass)
    • flow imbalance (CALL ≥1.20, PUT ≤0.80, optional)
    • vol surface can veto or upgrade (optional)
    • aggressive RR: tp=5R, sl=0.35R, trail=1.3

Standard Mode (fallback):
    • VWAP reclaim or rejection confirmed
    • moderate volume & flow
    • normal RR: tp=3R, sl=0.5R, trail=1.2
"""


class MorningBreakout:
    """
    WS-native breakout strategy.

    Consumes enriched snapshots from Orchestrator with VWAP metrics.
    Gracefully degrades when microstructure data unavailable.
    """

    def __init__(self, telemetry=None):
        self.telemetry = telemetry

        # Microstructure thresholds (optional enhancements)
        self.MIN_UPVOL = 60
        self.MIN_FLOW_CALL = 1.20
        self.MAX_FLOW_PUT = 0.80

        # Slope thresholds to avoid random noise
        self.MIN_SLOPE_UP = 0.00
        self.MIN_SLOPE_DN = 0.00

        # Morning-only window
        self.MORNING_LIMIT_SEC = 5400  # first 90 minutes (9:30 - 11:00 AM)

    # ---------------------------------------------------------
    def _is_morning(self, secs: float) -> bool:
        """Check if within morning trading window."""
        return secs <= self.MORNING_LIMIT_SEC

    # ---------------------------------------------------------
    def _vol_support(self, iv_chg, skew) -> bool:
        """Check if vol surface supports directional move."""
        return iv_chg is not None and skew is not None and iv_chg > 0 and skew > 0

    # ---------------------------------------------------------
    def _vol_against(self, iv_chg, skew) -> bool:
        """Check if vol surface contradicts directional move."""
        return iv_chg is not None and skew is not None and iv_chg < 0 and skew < 0

    # =========================================================
    # MAIN QUALIFIER
    # =========================================================
    def qualify(self, snap: dict):
        """
        Evaluate enriched snapshot for breakout signal.

        Expected snapshot format (from Orchestrator):
        {
            "symbol": str,
            "price": float,
            "vwap": float,                      # Computed by orchestrator
            "vwap_dev": float,                  # price - vwap
            "vwap_dev_change": float,           # Change in deviation
            "seconds_since_open": float,

            # Optional microstructure (may be None):
            "upvol_pct": float | None,
            "flow_ratio": float | None,
            "iv_change": float | None,
            "skew_shift": float | None,
        }

        Returns:
            Signal dict with:
            {
                "bias": "CALL" | "PUT",
                "regime": str,
                "grade": str,
                "vol_path": str,
                "tp_mult": float,
                "sl_mult": float,
                "trail_mult": float,
            }
            OR None if no signal
        """

        # =====================================================
        # REQUIRED FIELDS VALIDATION
        # =====================================================
        price = snap.get("price")
        vwap = snap.get("vwap")
        secs = snap.get("seconds_since_open", 0)

        if price is None or vwap is None:
            return None

        if not self._is_morning(secs):
            return None

        # =====================================================
        # CORE VWAP METRICS (always available from orchestrator)
        # =====================================================
        dev = snap.get("vwap_dev", 0)
        slope = snap.get("vwap_dev_change", 0)

        # =====================================================
        # OPTIONAL MICROSTRUCTURE FIELDS
        # Default to passing values if unavailable
        # =====================================================
        upvol = snap.get("upvol_pct")
        flow = snap.get("flow_ratio")
        ivc = snap.get("iv_change")
        skew = snap.get("skew_shift")

        # =====================================================
        # LAYER 1 — STRUCTURE (pre-reclaim / pre-reject)
        # =====================================================
        pre_call = price < vwap and slope > self.MIN_SLOPE_UP
        pre_put = price > vwap and slope < self.MIN_SLOPE_DN

        # =====================================================
        # LAYER 2 — MICROSTRUCTURE (optional enhancement)
        # =====================================================
        # If microstructure data unavailable, default to passing
        # This allows strategy to work with price/VWAP only

        if upvol is not None and flow is not None:
            # Full microstructure available
            micro_call = upvol >= self.MIN_UPVOL and flow >= self.MIN_FLOW_CALL
            micro_put = upvol >= self.MIN_UPVOL and flow <= self.MAX_FLOW_PUT
        else:
            # No microstructure - default to passing
            # Strategy degrades gracefully to price action only
            micro_call = True
            micro_put = True

        # =====================================================
        # LAYER 3 — VOL SURFACE (optional booster OR veto)
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
