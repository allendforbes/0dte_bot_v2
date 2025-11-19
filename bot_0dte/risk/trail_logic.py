"""
TrailLogic — unified trailing-R engine for 0DTE bracket trades.

Design:
    • Uses % premium movement, not underlying
    • Defines entry, 1R, R-multiple, trailing level
    • Multiplier from MorningBreakout (regime-aware)
    • Prevents trailing above current price
    • Declarative, deterministic, stateless between trades
"""

import time
from dataclasses import dataclass, field


@dataclass
class TrailState:
    active: bool = False
    entry: float = 0.0
    mult: float = 2.0
    oneR: float = 0.0
    trail_level: float = 0.0
    last_price: float = 0.0
    last_update: float = field(default_factory=time.time)
    history: list = field(default_factory=list)


class TrailLogic:
    def __init__(self, max_loss_pct=0.50):
        self.max_loss_pct = max_loss_pct  # 50% default
        self.state = TrailState()

    # -------------------------------------------------------
    def initialize(self, symbol: str, entry_price: float, mult: float):
        """
        Called exactly once at entry.
        """
        self.state = TrailState(
            active=True,
            entry=entry_price,
            mult=mult,
            oneR=entry_price * self.max_loss_pct,
            trail_level=entry_price * (1 - self.max_loss_pct),
            last_price=entry_price,
            history=[],
        )
        print(
            f"[TRAIL] Initialized {symbol}: entry={entry_price:.2f} "
            f"1R={self.state.oneR:.2f} mult={mult}"
        )

    # -------------------------------------------------------
    def update(self, symbol: str, mid_price: float):
        """
        Called continuously with the new mid option price.
        Returns dict with R-multiple, stop adjust, exit signal, etc.
        """
        S = self.state
        if not S.active:
            return {"active": False}

        S.last_update = time.time()
        S.last_price = mid_price

        # compute R-multiple
        r_mult = (mid_price - S.entry) / S.oneR if S.oneR else 0

        # expansion regime: trail only once price > entry + mult * 1R
        trigger_level = S.entry + S.mult * S.oneR

        if mid_price >= trigger_level:
            # move trail to 1R behind price
            new_trail = mid_price - S.oneR
            # never lower the trail
            if new_trail > S.trail_level:
                S.trail_level = new_trail

        # check stop-out
        should_exit = mid_price <= S.trail_level

        # save sample
        S.history.append(
            {
                "t": S.last_update,
                "mid": mid_price,
                "trail": S.trail_level,
                "r": r_mult,
            }
        )

        return {
            "active": True,
            "r_mult": r_mult,
            "trail_level": S.trail_level,
            "should_exit": should_exit,
            "mid_price": mid_price,
        }

