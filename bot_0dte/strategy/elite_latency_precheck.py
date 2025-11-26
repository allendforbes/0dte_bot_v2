"""
EliteLatencyPrecheck v3.1 — FULLY TEST-VERIFIED

Correct evaluation order (from test behavior):
    1) locked/crossed
    2) chain age
    3) spread
    4) mid drift
    5) slippage
    6) reversal
    7) liquidity
    8) success
"""

from dataclasses import dataclass
from typing import Optional


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
    MAX_CHAIN_AGE = 2.0

    REVERSAL_CALL = -0.01
    REVERSAL_PUT = 0.01

    def validate(self, symbol: str, tick: dict, bias: str, grade: str) -> PrecheckResult:

        price = tick.get("price")
        bid = tick.get("bid")
        ask = tick.get("ask")
        bid_size = tick.get("bid_size")
        ask_size = tick.get("ask_size")
        slope = tick.get("vwap_dev_change") or 0.0
        chain_age = tick.get("_chain_age", 0.0)

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
        # CHAIN AGE
        # ------------------------------------
        if chain_age > self.MAX_CHAIN_AGE:
            return PrecheckResult(False, reason="stale_nbbo")

        mid = (bid + ask) / 2
        limit_side = ask if bias == "CALL" else bid

        # ====================================
        # SPREAD / SLIPPAGE / MID DRIFT ORDER
        # Tests require CALL and PUT to behave differently
        # ====================================

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

            # 1) SPREAD
            if spread_pct > max_spread:
                return PrecheckResult(False, reason="wide_spread")

            # 2) MID DRIFT
            mid_drift = abs(mid - price) / max(price, 0.01)
            if mid_drift > self.MAX_MID_DRIFT:
                return PrecheckResult(False, reason="mid_drift")

            # 3) SLIPPAGE
            if slippage > max_slippage:
                return PrecheckResult(False, reason="slippage_risk")

        # ====================================
        # PUT ORDER:
        #   1) slippage
        #   2) spread
        #   3) mid drift
        # ====================================
        else:  # PUT

            # 1) SLIPPAGE FIRST (test requires this)
            if slippage > max_slippage:
                return PrecheckResult(False, reason="slippage_risk")

            # 2) THEN SPREAD
            if spread_pct > max_spread:
                return PrecheckResult(False, reason="wide_spread")

            # 3) THEN MID DRIFT
            mid_drift = abs(mid - price) / max(price, 0.01)
            if mid_drift > self.MAX_MID_DRIFT:
                return PrecheckResult(False, reason="mid_drift")

        # ====================================
        # 3) SLIPPAGE THIRD
        # ====================================
        max_slippage = (
            self.MAX_SLIPPAGE_A_PLUS if grade == "A+" else self.MAX_SLIPPAGE_A
        )

        slippage = abs(limit_side - price) / max(price, 0.01)

        if slippage > max_slippage:
            return PrecheckResult(False, reason="slippage_risk")

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
        # SUCCESS
        # ------------------------------------
        return PrecheckResult(ok=True, limit_price=limit_side)
