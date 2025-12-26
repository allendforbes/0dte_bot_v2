"""
ExecutionEngine — Production Version (Option-A Adaptive Timeout)
---------------------------------------------------------------
- IBKR used ONLY for order routing (not data)
- Massive NBBO drives execution & trailing logic
- Live & Paper supported
- Mock mode fully compatible
"""

import asyncio
import time
from dataclasses import dataclass, field
from ib_insync import IB, Option, MarketOrder
from bot_0dte.infra.phase import ExecutionPhase


# ================================================================
# Account State Object
# ================================================================
@dataclass
class AccountState:
    net_liq: float = 25000.0
    last_update: float = field(default_factory=time.time)

    def update(self, nlv: float):
        self.net_liq = nlv
        self.last_update = time.time()

    def is_fresh(self, max_age_sec: float = 20.0) -> bool:
        return (time.time() - self.last_update) <= max_age_sec


# ================================================================
# Execution Engine
# ================================================================
class ExecutionEngine:
    """
    Responsibilities:
        • Maintain NetLiq state
        • Provide async-safe send_bracket() & send_market()
        • Support mock + paper + live
        • IBKR required ONLY for routing orders
    """

    def __init__(self, use_mock: bool = True, execution_phase: ExecutionPhase = None):
        self.use_mock = use_mock
        self.execution_phase = execution_phase or ExecutionPhase.SHADOW
        self.ib: IB | None = None
        self.account_state = AccountState()
        self.expiry_map = {}  # filled by orchestrator
        print(f"[EXEC] Engine initialized (phase={self.execution_phase.value}, mock={use_mock}).")

    # ------------------------------------------------------------
    async def attach_ib(self, ib: IB):
        """Attach connected IBKR instance."""
        self.ib = ib
        print("[EXEC] Attached IBKR instance.")
        
        # Log IBKR ready state for debugging
        client_id = getattr(ib.client, 'clientId', 'unknown') if hasattr(ib, 'client') else 'unknown'
        print(f"[EXEC] IBKR execution ready: client_id={client_id}")

    # ------------------------------------------------------------
    async def start(self):
        """Load NetLiq at startup."""
        if self.use_mock or not self.ib:
            print("[EXEC] Mock engine initialized — NetLiq=25,000.")
            self.account_state.update(25000)
            print("[EXEC] ibkr_execution_ready: mock=True, connected=False")
            return

        if not self.ib:
            raise RuntimeError("ExecutionEngine.start() called but self.ib is None")

        acct = await self.ib.accountSummaryAsync()
        for row in acct:
            if row.tag == "NetLiquidation":
                self.account_state.update(float(row.value))
                break

        # Log execution ready
        client_id = getattr(self.ib.client, 'clientId', 'unknown') if hasattr(self.ib, 'client') else 'unknown'
        print(f"[EXEC] Live IBKR NetLiq loaded: {self.account_state.net_liq}")
        print(f"[EXEC] ibkr_execution_ready: connected=True, client_id={client_id}")

    # ============================================================
    # CONTRACT CONSTRUCTION
    # ============================================================
    def _ib_contract_for(self, symbol: str, right: str, strike: float):
        expiry = self.expiry_map.get(symbol)
        if not expiry:
            raise RuntimeError(f"No expiry configured for {symbol}.")

        yyyy, mm, dd = expiry.split("-")
        yyyymmdd = f"{yyyy}{mm}{dd}"

        return Option(
            symbol,
            yyyymmdd,
            strike,
            right[0].upper(),  # CALL -> C, PUT -> P
            "SMART",
        )

    # ------------------------------------------------------------
    def _enforce_execution_phase(self):
        """Hard guard: SHADOW phase cannot execute orders."""
        if self.execution_phase == ExecutionPhase.SHADOW:
            raise RuntimeError(
                "FATAL: Order execution attempted in SHADOW phase. "
                "SHADOW mode is log-only and must never place orders."
            )

    # ============================================================
    # MAIN BRACKET EXECUTION
    # ============================================================
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
        Entry execution for CALL/PUT.
        IBKR → Limit order at entry_price with adaptive timeout.
        """

        self._enforce_execution_phase()

        # ------------------------------
        # MOCK MODE
        # ------------------------------
        if self.use_mock or not self.ib:
            return await self._mock_fill(
                symbol, side, qty, entry_price, take_profit, stop_loss, meta
            )

        # ------------------------------
        # IBKR VERIFICATION (CRITICAL)
        # ------------------------------
        if not self.ib:
            print("[EXEC][ERROR] send_bracket() called but self.ib is None")
            return {
                "status": "error",
                "error": "IBKR not connected",
                "symbol": symbol,
            }

        # ------------------------------
        # LIVE / PAPER MODE
        # ------------------------------
        try:
            right = "C" if side.upper() == "CALL" else "P"
            strike = meta["strike"]

            contract = self._ib_contract_for(symbol, right, strike)

            order = self.ib.limitOrder(
                action="BUY",   # ALWAYS BUY TO OPEN (CALL or PUT)
                totalQuantity=qty,
                lmtPrice=entry_price,
            )

            trade = self.ib.placeOrder(contract, order)

            timeout = 5.0 if self._is_opening_range() else 3.0
            filled = await self._wait_for_fill(trade, timeout)

            if not filled:
                print("[EXEC][LIVE] Entry timeout → cancelling order.")
                self.ib.cancelOrder(order)
                return {"status": "cancelled_timeout", "symbol": symbol}

            fill_price = float(trade.fills[-1].execution.price)

            print(f"[EXEC][LIVE] Entry filled @ {fill_price:.2f}")

            return {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "entry": fill_price,
                "tp": take_profit,
                "sl": stop_loss,
                "meta": meta,
                "status": "filled",
            }

        except Exception as e:
            print(f"[EXEC][LIVE][ERROR] {e}")
            return {"status": "error", "error": str(e)}

    # ============================================================
    # MARKET EXIT EXECUTION
    # ============================================================
    async def send_market(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float | None,
        meta: dict,
    ):
        """Used for exits (trail_exit, hard_stop, etc.)."""

        self._enforce_execution_phase()

        # MOCK MODE
        if self.use_mock or not self.ib:
            print(f"[EXEC][MOCK] Market exit for {symbol}: {side} x{qty}")
            await asyncio.sleep(0.05)
            return {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "status": "mock-market-fill",
                "price": price,
                "meta": meta,
            }

        # LIVE/PAPER MODE
        try:
            right = "C" if side.upper() == "CALL" else "P"
            strike = meta.get("strike")
            contract = self._ib_contract_for(symbol, right, strike)

            order = MarketOrder(
                action="SELL",   # ALWAYS SELL TO CLOSE (CALL or PUT)
                totalQuantity=qty,
            )


            trade = self.ib.placeOrder(contract, order)

            timeout = 3.0
            await asyncio.sleep(timeout)

            if trade.fills:
                px = float(trade.fills[-1].execution.price)
                print(f"[EXEC][LIVE] Market exit filled @ {px:.2f}")
                return {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "status": "market_filled",
                    "price": px,
                    "meta": meta,
                }

            print("[EXEC][LIVE] Market exit submitted (fill unknown).")
            return {"status": "submitted", "symbol": symbol}

        except Exception as e:
            print(f"[EXEC][LIVE][ERROR] {e}")
            return {"status": "error", "error": str(e)}

    # ============================================================
    # SUPPORT FUNCTIONS
    # ============================================================
    async def _wait_for_fill(self, trade, timeout: float):
        """Return True when a fill occurs."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if trade.fills:
                return True
            await asyncio.sleep(0.05)
        return False

    def _is_opening_range(self):
        """Market open = first 3 minutes."""
        now = time.localtime()
        return now.tm_hour == 9 and now.tm_min < 33

    # ============================================================
    # MOCK SIMULATION
    # ============================================================
    async def _mock_fill(self, symbol, side, qty, entry, tp, sl, meta):
        print(f"[EXEC][MOCK] Bracket simulated for {symbol}: entry={entry:.2f}")
        print(f"[EXEC][MOCK] Phase={self.execution_phase.value}, Mock=True")
        await asyncio.sleep(0.10)
        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "meta": meta,
            "status": "mock-filled",
            "phase": self.execution_phase.value,
            "mock": True,
        }