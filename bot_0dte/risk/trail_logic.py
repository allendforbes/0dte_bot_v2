"""
TrailLogic — Unified trailing-R engine for 0DTE options.

Rules:
    • 1R = entry_price * max_loss_pct
    • Trail starts at entry - 1R (e.g., -50%)
    • Trail lifts once price >= entry + mult·1R
    • Trail = mid - 1R (but never lower previous)
    • Stop-out if mid <= trail
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
        self.max_loss_pct = max_loss_pct
        self.state = TrailState()

    # -----------------------------------------------------------
    def initialize(self, symbol: str, entry_price: float, mult: float):
        oneR = entry_price * self.max_loss_pct
        self.state = TrailState(
            active=True,
            entry=entry_price,
            mult=mult,
            oneR=oneR,
            trail_level=entry_price - oneR,   # e.g. -50%
            last_price=entry_price,
            history=[],
        )
        return {
            "entry": entry_price,
            "oneR": oneR,
            "trail_start": entry_price - oneR,
            "mult": mult,
        }

    # -----------------------------------------------------------
    def update(self, symbol: str, mid_price: float):
        S = self.state
        if not S.active:
            return {"active": False}

        S.last_update = time.time()
        S.last_price = mid_price

        # R multiple
        r_mult = (mid_price - S.entry) / S.oneR if S.oneR else 0.0

        # Level where trailing kicks in
        trigger = S.entry + S.mult * S.oneR

        # If above trigger → raise trail
        if mid_price >= trigger:
            new_trail = mid_price - S.oneR
            if new_trail > S.trail_level:
                S.trail_level = new_trail

        # Exit condition
        should_exit = mid_price <= S.trail_level

        # Save history snapshot
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
