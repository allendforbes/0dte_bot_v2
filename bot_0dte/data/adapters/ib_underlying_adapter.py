"""
IBUnderlyingAdapter v5.0 — Guaranteed Tick Delivery
Ultra-clean, event-loop-safe, reconnect-safe, stale-session-proof.
"""

import asyncio
import time
import logging
from typing import Callable, List

from ib_insync import IB, Stock, Ticker

logger = logging.getLogger(__name__)


class IBUnderlyingAdapter:
    RECONNECT_INTERVAL = 7
    WATCHDOG_INTERVAL = 5

    def __init__(self, host="127.0.0.1", port=4002, client_id=None, loop=None):
        self.host = host
        self.port = port
        self.loop = loop or asyncio.get_event_loop()

        # ⭐ NEW: clientId rotation table (prevents stale-Gateway state)
        self.client_ids = [11, 111, 211, 311]
        self.client_id_index = 0
        if client_id is not None:
            # Allow overriding rotation start
            self.client_ids[0] = client_id

        self.client_id = self.client_ids[self.client_id_index]

        # Core IBKR client
        self.ib = IB()

        # Live subscriptions
        self.symbols: List[str] = []
        self._contracts = {}
        self._tickers = {}

        # State
        self._connected = False
        self._reconnecting = False
        self._handlers: List[Callable] = []
        self._watchdog_task = None
        self.parent_orchestrator = None

        self.debug_ticks = False  # Heavy tick logging

    # ============================================================
    def _next_client_id(self):
        """Rotate to next clientId to break stale IBKR Gateway sessions."""
        self.client_id_index = (self.client_id_index + 1) % len(self.client_ids)
        self.client_id = self.client_ids[self.client_id_index]
        logger.error(f"[IB] Rotating clientId → {self.client_id}")

    # ============================================================
    def on_underlying(self, cb: Callable):
        self._handlers.append(cb)

    # ============================================================
    async def connect(self):
        """
        Connect with automatic clientId failover.
        Validates LIVE streaming market data before returning success.
        """
        attempts = len(self.client_ids)

        for attempt in range(attempts):
            cid = self.client_id
            logger.info(f"[IB UNDERLYING] Connecting (clientId={cid}) → {self.host}:{self.port}...")

            try:
                await self.ib.connectAsync(
                    host=self.host,
                    port=self.port,
                    clientId=cid,
                    timeout=15,
                )
            except Exception as e:
                logger.error(f"[IB UNDERLYING] connectAsync failed ({cid}): {e}")
                self._next_client_id()
                continue

            self._connected = True
            logger.info(f"[IB UNDERLYING] Connected (clientId={cid}) ✅")

            # ⭐ Always force LIVE mode
            self.ib.reqMarketDataType(1)

            # ⭐ Verify LIVE market data actually streams
            ok = await self._verify_live_data()
            if ok:
                logger.info("[IB] Live market data verified ✔")

                if not self._watchdog_task:
                    self._watchdog_task = self.loop.create_task(self._connection_watchdog())

                return  # SUCCESS

            # ❌ No ticks — session stale → rotate clientId
            logger.error(f"[IB] No ticks after connect (clientId={cid}) → rotating...")
            try:
                self.ib.disconnect()
            except Exception:
                pass

            self._connected = False
            self._next_client_id()

        # If all clientIds fail → hard failure
        raise RuntimeError("IBKR underlying feed failed for ALL clientIds")

    # ============================================================
    async def _verify_live_data(self):
        """
        Qualify SPY and request a tiny L1 stream.
        If ticks arrive → session is good.
        """
        try:
            test = Stock("SPY", "SMART", "USD")
            [test] = await self.ib.qualifyContractsAsync(test)

            ticker = self.ib.reqMktData(test)
            await asyncio.sleep(1.5)

            if ticker.ticks:
                return True

            return False

        except Exception as e:
            logger.error(f"[IB] Live MD verification failed: {e}")
            return False

    # ============================================================
    async def subscribe(self, symbols: List[str]):
        if not self._connected:
            raise RuntimeError("Connect before subscribing.")

        logger.info(f"[IB UNDERLYING] Subscribing: {symbols}")
        self.symbols = symbols

        for symbol in symbols:
            await self._subscribe_symbol(symbol)

        logger.info("[IB UNDERLYING] Subscriptions active")

    # ============================================================
    async def _subscribe_symbol(self, symbol: str):
        try:
            contract = Stock(symbol, "SMART", "USD")
            qualified = await self.ib.qualifyContractsAsync(contract)
            if not qualified:
                logger.error(f"[IB UNDERLYING] Failed to qualify {symbol}")
                return

            c = qualified[0]
            self._contracts[symbol] = c

            ticker: Ticker = self.ib.reqMktData(c, "", False, False)
            self._tickers[symbol] = ticker

            ticker.updateEvent += lambda t, sym=symbol: self._on_ticker_update(sym, t)

            logger.info(f"[IB UNDERLYING] Subscribed: {symbol}")

        except Exception as e:
            logger.exception(f"[IB UNDERLYING] Error subscribing {symbol}: {e}")

    # ============================================================
    def _on_ticker_update(self, symbol: str, ticker: Ticker):
        """Raw IBKR update handler — synchronous, minimal CPU."""
        try:
            price = ticker.last or ticker.close
            if not price or price <= 0:
                return

            bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
            ask = ticker.ask if ticker.ask and ticker.ask > 0 else None

            if self.debug_ticks:
                print(f"[IB RAW] {symbol} last={price} bid={bid} ask={ask}")

            event = {
                "symbol": symbol,
                "price": float(price),
                "bid": float(bid) if bid else None,
                "ask": float(ask) if ask else None,
                "_recv_ts": time.time(),
            }

            for cb in self._handlers:
                self.loop.create_task(self._safe_callback(cb, event))

        except Exception:
            logger.exception("[IB UNDERLYING] Ticker update failed")

    # ============================================================
    async def _safe_callback(self, cb, event):
        try:
            await cb(event)
        except Exception:
            logger.exception("[IB UNDERLYING] Handler callback failed")

    # ============================================================
    async def _connection_watchdog(self):
        """
        Periodically checks IBKR connection.
        If disconnected → triggers reconnect with full clientId rotation logic.
        """
        try:
            while True:
                await asyncio.sleep(self.WATCHDOG_INTERVAL)

                if self.ib.isConnected():
                    continue

                if not self._reconnecting:
                    self.loop.create_task(self._attempt_reconnect())

        except asyncio.CancelledError:
            pass

    # ============================================================
    async def _attempt_reconnect(self):
        self._reconnecting = True
        logger.error("[IB UNDERLYING] Connection lost — attempting reconnect...")

        if self.parent_orchestrator:
            try:
                self.parent_orchestrator.notify_underlying_down()
            except Exception:
                logger.exception("[IB] Failed to notify orchestrator (down)")

        # Attempt reconnect with rotation
        while not self.ib.isConnected():
            try:
                await asyncio.sleep(self.RECONNECT_INTERVAL)

                logger.info(f"[IB] Reconnect attempt (clientId={self.client_id})")
                await self.ib.connectAsync(
                    host=self.host,
                    port=self.port,
                    clientId=self.client_id,
                    timeout=10,
                )

                # Always force LIVE mode
                self.ib.reqMarketDataType(1)

                # Verify streaming
                ok = await self._verify_live_data()
                if ok:
                    logger.info("[IB] Reconnected & streaming ✔")
                    break

                logger.error("[IB] Reconnected but NO ticks — rotating...")
                self.ib.disconnect()
                self._next_client_id()

            except Exception:
                logger.error("[IB] Reconnect attempt failed")
                self._next_client_id()
                continue

        # Resubscribe if success
        if self.symbols:
            await self.subscribe(self.symbols)

        if self.parent_orchestrator:
            try:
                self.parent_orchestrator.notify_underlying_recovered()
            except Exception:
                logger.exception("[IB] Failed to notify orchestrator (recovered)")

        self._reconnecting = False
