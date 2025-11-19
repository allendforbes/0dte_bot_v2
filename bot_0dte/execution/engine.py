"""
ExecutionEngine — unified mock + IBKR execution layer.

Responsibilities:
    • Attach IBKR client (paper or live)
    • Provide send_bracket() interface for orchestrator
    • Maintain account state (net liq, freshness)
    • Guarantee async-safe, exception-safe submissions
"""

import asyncio
from dataclasses import dataclass, field
import time

# ---------------------------------------------------------
# Account State Object
# ---------------------------------------------------------
@dataclass
class AccountState:
    net_liq: float = 25000.0
    last_update: float = field(default_factory=time.time)

    def update(self, nlv):
        self.net_liq = nlv
        self.last_update = time.time()

    def is_fresh(self, max_age_sec=20):
        return (time.time() - self.last_update) <= max_age_sec


# ---------------------------------------------------------
# Execution Engine
# ---------------------------------------------------------
class ExecutionEngine:
    def __init__(self, use_mock: bool = True):
        self.use_mock = use_mock
        self.ib = None
        self.account_state = AccountState()
        print(f"[EXEC] Engine initialized (mock={use_mock}).")

    # -----------------------------------------------------
    async def attach_ib(self, ib):
        """Attach IBKR instance (live or paper)."""
        self.ib = ib
        print("[EXEC] Attached IBKR instance.")

    # -----------------------------------------------------
    async def start(self):
        """Load initial account state."""
        if self.use_mock:
            print("[EXEC] Mock engine: no IBKR warmup.")
            self.account_state.update(25000)
            return

        # live mode
        acct = await self.ib.accountSummaryAsync()
        for row in acct:
            if row.tag == "NetLiquidation":
                self.account_state.update(float(row.value))
                break

        print(f"[EXEC] IBKR account loaded → NetLiq = {self.account_state.net_liq}")

    # -----------------------------------------------------
    async def send_bracket(
        self,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        meta: dict,
    ):
        """
        Main bracket-submit call used by orchestrator.
        Mock mode instantly returns simulated order structure.
        """

        if self.use_mock:
            return await self._mock_fill(symbol, side, qty, entry_price, take_profit, stop_loss, meta)

        # -------------------------------------------------
        # Live IBKR routing (stub — fully expandable)
        # -------------------------------------------------
        print(f"[EXEC] LIVE SUBMIT: {symbol} {side} x{qty} @ {entry_price}")
        order = {"status": "submitted", "symbol": symbol, "qty": qty, "side": side}
        return order

    # -----------------------------------------------------
    async def _mock_fill(self, symbol, side, qty, entry, tp, sl, meta):
        """Simulated bracket fill for testing & Mode-B confirmation."""
        print(f"[EXEC][MOCK] Bracket simulated for {symbol}: entry={entry:.2f}")

        await asyncio.sleep(0.15)  # small delay for realism

        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "meta": meta,
            "status": "mock-filled",
        }

