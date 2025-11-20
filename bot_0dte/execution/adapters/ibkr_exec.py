import asyncio
import logging
from typing import Optional, Callable, Dict, Any
from ib_insync import IB, Stock, Option, MarketOrder, LimitOrder, StopOrder

from bot_0dte.config import EXECUTION_MODE, ENABLE_LIVE_TRADING


class IBKRExecAdapter:
    """
    Institutional-grade async execution adapter for IBKR.

    - Supports PAPER or LIVE mode.
    - Enforces config-driven safety rails.
    - Async, event-driven fill detection.
    - ExecutionEngine-compatible: send_bracket(), send_single(), cancel().
    - No implicit mode switching.
    """

    VALID_PAPER_TYPES = {"PAPER", "SIM"}
    VALID_LIVE_TYPES = {"INDIVIDUAL", "LIVE"}

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7496,
        client_id: int = 110,
    ):

        self.host = host
        self.port = port
        self.client_id = client_id

        self.mode = EXECUTION_MODE  # "paper" or "live"
        self.enable_live = ENABLE_LIVE_TRADING

        self.logger = logging.getLogger("ibkr_exec_adapter")

        self.ib = IB()
        self.connected = False

        # For async orderStatus event handling
        self._order_events: Dict[int, asyncio.Future] = {}

    # ============================================================
    # Connection & Account Validation
    # ============================================================

    async def connect(self):
        await self.ib.connectAsync(self.host, self.port, self.client_id)
        if not self.ib.isConnected():
            raise RuntimeError(f"IBKR connection failed {self.host}:{self.port}")

        self.connected = True
        self.logger.info(
            f"[IBKR] Connected: mode={self.mode} client_id={self.client_id}"
        )

        acct_type = await self._read_account_type()
        self._validate_account_type(acct_type)

        # Subscribe to orderStatus events
        self.ib.orderStatusEvent += self._on_order_status_event

    async def disconnect(self):
        if self.connected:
            self.ib.disconnect()
            self.connected = False

    async def _read_account_type(self) -> str:
        vals = await self.ib.reqAccountSummaryAsync()
        for v in vals:
            if v.tag == "AccountType":
                return v.value.upper()
        return "UNKNOWN"

    def _validate_account_type(self, acct_type: str):
        if self.mode == "paper":
            if acct_type not in self.VALID_PAPER_TYPES:
                raise RuntimeError(
                    f"Execution mode=paper but IBKR account is LIVE: {acct_type}"
                )
            return

        if self.mode == "live":
            if not self.enable_live:
                raise RuntimeError(
                    "Live trading blocked by config: ENABLE_LIVE_TRADING=False"
                )
            if acct_type not in self.VALID_LIVE_TYPES:
                raise RuntimeError(
                    f"Execution mode=live but IBKR account is not LIVE: {acct_type}"
                )
            return

        raise ValueError(f"Unknown EXECUTION_MODE: {self.mode}")

    # ============================================================
    # Contract Builders
    # ============================================================

    @staticmethod
    def build_stock(symbol: str):
        return Stock(symbol, "SMART", "USD")

    @staticmethod
    def build_option(symbol: str, expiry: str, strike: float, right: str):
        return Option(symbol, expiry, strike, right.upper(), "SMART", "USD")

    # ============================================================
    # OrderStatus Event Handler
    # ============================================================

    def _on_order_status_event(self, trade):
        oid = trade.order.orderId
        if oid in self._order_events:
            fut = self._order_events[oid]
            if not fut.done():
                fut.set_result(trade)

    async def _wait_for_fill_or_submit(self, oid: int, timeout: float = 4.0):
        fut = asyncio.get_event_loop().create_future()
        self._order_events[oid] = fut

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._order_events.pop(oid, None)

    # ============================================================
    # Core Order Senders
    # ============================================================

    async def send_single(self, req: Dict[str, Any]) -> Dict[str, Any]:
        t0 = self.ib.time()
        contract = self._build_contract_from_req(req)
        action = req["side"].upper()

        if req["order_type"] == "MKT":
            order = MarketOrder(action, req["qty"])
        elif req["order_type"] == "LMT":
            order = LimitOrder(action, req["qty"], req["limit_price"])
        else:
            raise ValueError(f"Unknown order_type: {req['order_type']}")

        trade = self.ib.placeOrder(contract, order)

        evt = await self._wait_for_fill_or_submit(order.orderId)
        latency_ms = (self.ib.time() - t0) * 1000

        result = {
            "ok": evt is not None,
            "symbol": req["symbol"],
            "order_id": order.orderId,
            "status": evt.orderStatus.status if evt else "UNKNOWN",
            "mode": self.mode,
            "latency_ms": latency_ms,
        }

        return result

    async def send_bracket(self, req: Dict[str, Any]) -> Dict[str, Any]:
        t0 = self.ib.time()
        contract = self._build_contract_from_req(req)

        action = "BUY" if req["side"].upper() == "CALL" else "SELL"

        parent = MarketOrder(action, req["qty"])
        parent.transmit = False

        tp_action = "SELL" if action == "BUY" else "BUY"
        tp_order = LimitOrder(tp_action, req["qty"], req["take_profit"])
        tp_order.parentId = None
        tp_order.transmit = False

        sl_action = "SELL" if action == "BUY" else "BUY"
        sl_order = StopOrder(sl_action, req["qty"], req["stop_loss"])
        sl_order.parentId = None
        sl_order.transmit = True

        parent_trade = self.ib.placeOrder(contract, parent)
        parent_id = parent.orderId

        tp_order.parentId = parent_id
        sl_order.parentId = parent_id

        self.ib.placeOrder(contract, tp_order)
        self.ib.placeOrder(contract, sl_order)

        evt = await self._wait_for_fill_or_submit(parent_id)
        latency_ms = (self.ib.time() - t0) * 1000

        result = {
            "ok": evt is not None,
            "symbol": req["symbol"],
            "entry_order_id": parent_id,
            "tp_order_id": tp_order.orderId,
            "sl_order_id": sl_order.orderId,
            "status": evt.orderStatus.status if evt else "UNKNOWN",
            "mode": self.mode,
            "latency_ms": latency_ms,
        }

        return result

    # ============================================================
    # Cancel
    # ============================================================

    async def cancel(self, order_id: int) -> Dict[str, Any]:
        try:
            self.ib.cancelOrder(order_id)
            return {"ok": True, "order_id": order_id, "mode": self.mode}
        except Exception as e:
            return {"ok": False, "order_id": order_id, "error": str(e)}

    # ============================================================
    # Helpers
    # ============================================================

    def _build_contract_from_req(self, req: Dict[str, Any]):
        ct = req.get("contract_type", "OPT")
        if ct == "STK":
            return self.build_stock(req["symbol"])
        if ct == "OPT":
            return self.build_option(
                req["symbol"], req["expiry"], req["strike"], req["right"]
            )
        raise ValueError(f"Unknown contract_type: {ct}")
