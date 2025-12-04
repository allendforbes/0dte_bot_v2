"""
EliteLatencyPrecheck v3.2 — A2-M Enhanced
-----------------------------------------
Maintains v3.1 order-of-operations guarantee but adds:

A2-M Upgrades:
    • Premium ceiling protection
    • Delta convexity guard
    • Gamma insufficiency rejection
    • Symbol-specific latency cap (from universe)
    • Millisecond chain age check
    • A2-M microstructure tightening
"""

from dataclasses import dataclass
from typing import Optional
from bot_0dte import universe


@dataclass
class PrecheckResult:
    ok: bool
    limit_price: Optional[float] = None
    reason: Optional[str] = None


class EliteLatencyPrecheck:

    MAX_SPREAD_A = 0.20
    MAX_SPREAD_A_PLUS = 0.30

    MAX_SLIPPAGE_A = 0.12
    MAX_SLIPPAGE_A_PLUS = 0.18

    MAX_MID_DRIFT = 0.10
    MIN_SIZE = 5

    # v3.1 used 2.0 seconds — A2-M uses ms implicitly
    MAX_CHAIN_AGE = 2.0  # sec fallback for old data

    REVERSAL_CALL = -0.01
    REVERSAL_PUT = 0.01

    # ---------------------------------------------------------
    # A2-M PARAMETERS
    # ---------------------------------------------------------
    MIN_GAMMA = 0.002          # dead-option protection
    MAX_DELTA_OFF_TARGET = 0.18  # delta too misaligned becomes toxic

    def validate(self, symbol: str, tick: dict, bias: str, grade: str, snap: dict = None) -> PrecheckResult:

        price = tick.get("price")
        bid = tick.get("bid")
        ask = tick.get("ask")
        bid_size = tick.get("bid_size")
        ask_size = tick.get("ask_size")
        slope = tick.get("vwap_dev_change") or 0.0

        # A2-M: allow `_chain_age_ms` OR fallback `_chain_age`
        chain_age_ms = tick.get("_chain_age_ms")
        chain_age_s = tick.get("_chain_age", 0.0)

        chain_age = (
            chain_age_ms / 1000.0 if chain_age_ms is not None else chain_age_s
        )

        delta = tick.get("delta")
        gamma = tick.get("gamma")

        # ------------------------------------
        # REQUIRED
        # ------------------------------------
        if price is None or bid is None or ask is None:
            return PrecheckResult(False, reason="missing_prices")

        if bid <= 0 or ask <= 0:
            return PrecheckResult(False, reason="invalid_quotes")

        # ------------------------------------
        # LOCKED / CROSSED
        # ------------------------------------
        if bid >= ask:
            return PrecheckResult(False, reason="locked_market")

        # ------------------------------------
        # CHAIN AGE (A2-M hot path check)
        # ------------------------------------
        if chain_age > self.MAX_CHAIN_AGE:
            return PrecheckResult(False, reason="stale_nbbo")

        mid = (bid + ask) / 2
        limit_side = ask if bias == "CALL" else bid

        spread = ask - bid
        spread_pct = spread / max(price, 0.01)
        max_spread = self.MAX_SPREAD_A_PLUS if grade == "A+" else self.MAX_SPREAD_A

        max_slippage = (
            self.MAX_SLIPPAGE_A_PLUS if grade == "A+" else self.MAX_SLIPPAGE_A
        )
        slippage = abs(limit_side - price) / max(price, 0.01)

        # ====================================
        # CALL ORDER:
        #   1) spread
        #   2) mid drift
        #   3) slippage
        # ====================================
        if bias == "CALL":

            if spread_pct > max_spread:
                return PrecheckResult(False, reason="wide_spread")

            mid_drift = abs(mid - price) / max(price, 0.01)
            if mid_drift > self.MAX_MID_DRIFT:
                return PrecheckResult(False, reason="mid_drift")

            if slippage > max_slippage:
                return PrecheckResult(False, reason="slippage_risk")

        # ====================================
        # PUT ORDER:
        #   1) slippage
        #   2) spread
        #   3) mid drift
        # ====================================
        else:

            if slippage > max_slippage:
                return PrecheckResult(False, reason="slippage_risk")

            if spread_pct > max_spread:
                return PrecheckResult(False, reason="wide_spread")

            mid_drift = abs(mid - price) / max(price, 0.01)
            if mid_drift > self.MAX_MID_DRIFT:
                return PrecheckResult(False, reason="mid_drift")

        # ------------------------------------
        # A2-M: PREMIUM CEILING PROTECTION
        # ------------------------------------
        ceiling = universe.max_premium(symbol)
        if mid > ceiling:
            return PrecheckResult(False, reason="premium_ceiling")

        # ------------------------------------
        # A2-M: DELTA & GAMMA CONVEXITY GUARD
        # ------------------------------------
        if delta is not None:
            # Too far from 0.30 / -0.30 loses asymmetry
            if (
                (bias == "CALL" and abs(delta - 0.30) > self.MAX_DELTA_OFF_TARGET)
                or (bias == "PUT" and abs(delta + 0.30) > self.MAX_DELTA_OFF_TARGET)
            ):
                return PrecheckResult(False, reason="delta_misaligned")

        if gamma is not None and gamma < self.MIN_GAMMA:
            return PrecheckResult(False, reason="low_gamma")

        # ------------------------------------
        # REVERSAL
        # ------------------------------------
        if bias == "CALL" and slope < self.REVERSAL_CALL:
            return PrecheckResult(False, reason="micro_reversal")
        if bias == "PUT" and slope > self.REVERSAL_PUT:
            return PrecheckResult(False, reason="micro_reversal")

        # ------------------------------------
        # LIQUIDITY
        # ------------------------------------
        if bid_size is not None and bid_size < self.MIN_SIZE:
            return PrecheckResult(False, reason="thin_liquidity")
        if ask_size is not None and ask_size < self.MIN_SIZE:
            return PrecheckResult(False, reason="thin_liquidity")

        # ------------------------------------
        # MICROSTRUCTURE PROTECTION
        # ------------------------------------
        if snap is not None:
            upvol_pct = snap.get("upvol_pct")
            if upvol_pct is not None:
                # A2-M tightened directional flow thresholds
                if bias == "CALL" and upvol_pct < 60:
                    return PrecheckResult(False, reason="weak_flow")
                if bias == "PUT" and upvol_pct > 40:
                    return PrecheckResult(False, reason="weak_flow")

            iv_change = snap.get("iv_change")
            if iv_change is not None and abs(iv_change) > 0.20:
                return PrecheckResult(False, reason="iv_spike")

        # ------------------------------------
        # A2-M: LATENCY PROTECTION (optional)
        # tick may include `latency_ms`
        # ------------------------------------
        latency_ms = tick.get("latency_ms")
        if latency_ms is not None:
            max_lat = universe.max_latency_ms(symbol)
            if latency_ms > max_lat:
                return PrecheckResult(False, reason="latency_exceeded")

        # ------------------------------------
        # SUCCESS
        # ------------------------------------
        return PrecheckResult(ok=True, limit_price=limit_side)
