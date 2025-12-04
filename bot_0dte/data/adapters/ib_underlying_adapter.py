"""
IBUnderlyingAdapter v3.0 — Event-Loop-Safe, Reconnect-Safe, Bot-Safe
Moderate reconnect cadence (7 seconds)
"""

import asyncio
import time
import logging
from typing import Callable, List

from ib_insync import IB, Stock, Ticker

logger = logging.getLogger(__name__)


class IBUnderlyingAdapter:
    RECONNECT_INTERVAL = 7      # moderate cadence
    WATCHDOG_INTERVAL = 5       # how often we check IB health

    def __init__(self, host="127.0.0.1", port=4002, client_id=11, loop=None):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.loop = loop or asyncio.get_event_loop()

        self.ib = IB()
        self.symbols: List[str] = []
        self._connected = False

        # IB objects
        self._contracts = {}
        self._tickers = {}

        # Orchestrator / Mux handlers
        self._handlers: List[Callable] = []

        # Watchdog task
        self._watchdog_task = None
        self._reconnecting = False  # prevent concurrent reconnects

        # Orchestrator pause/resume callbacks
        self.parent_orchestrator = None

    # ============================================================
    def on_underlying(self, cb: Callable):
        self._handlers.append(cb)

    # ============================================================
    async def connect(self):
        logger.info(f"[IB UNDERLYING] Connecting to {self.host}:{self.port}...")

        try:
            await self.ib.connectAsync(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=15
            )
        except Exception as e:
            logger.error(f"[IB UNDERLYING] Connection failed: {e}")
            raise

        self._connected = True
        logger.info("[IB UNDERLYING] Connected ✅")

        # Start watchdog only once
        if not self._watchdog_task:
            self._watchdog_task = self.loop.create_task(self._connection_watchdog())

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
    async def close(self):
        logger.info("[IB UNDERLYING] Closing...")

        if self._watchdog_task:
            self._watchdog_task.cancel()

        if self.ib.isConnected():
            self.ib.disconnect()

        self._connected = False
        logger.info("[IB UNDERLYING] Disconnected.")

    # ============================================================
    async def _subscribe_symbol(self, symbol: str):
        try:
            stock = Stock(symbol, "SMART", "USD")
            qualified = await self.ib.qualifyContractsAsync(stock)

            if not qualified:
                logger.error(f"[IB UNDERLYING] Failed to qualify {symbol}")
                return

            contract = qualified[0]
            self._contracts[symbol] = contract

            ticker = self.ib.reqMktData(contract)
            self._tickers[symbol] = ticker

            ticker.updateEvent += lambda t, sym=symbol: self._on_ticker_update(sym, t)

            logger.info(f"[IB UNDERLYING] Subscribed: {symbol}")

        except Exception as e:
            logger.error(f"[IB UNDERLYING] Error subscribing {symbol}: {e}")

    # ============================================================
    def _on_ticker_update(self, symbol: str, ticker: Ticker):
        """
        Fully protected callback: exceptions never escape into event loop.
        """
        try:
            price = ticker.last if ticker.last and ticker.last > 0 else ticker.close
            if not price or price <= 0:
                return

            bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
            ask = ticker.ask if ticker.ask and ticker.ask > 0 else None

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
        Runs every 5 seconds. If IB disconnects:
            • bot pauses
            • reconnect is attempted every 7s (moderate cadence)
            • bot resumes when IB recovers
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
        """
        Moderate reconnect loop (7s backoff)
        Never runs concurrently.
        Never touches event loop state.
        Never recursively calls connect inside connect().
        """

        self._reconnecting = True
        logger.error("[IB UNDERLYING] Connection lost — attempting reconnect...")

        # Notify orchestrator to PAUSE
        if self.parent_orchestrator:
            try:
                self.parent_orchestrator.notify_underlying_down()
            except Exception:
                logger.exception("[IB] Failed to notify orchestrator (down)")

        while not self.ib.isConnected():
            try:
                await asyncio.sleep(self.RECONNECT_INTERVAL)
                await self.ib.connectAsync(
                    host=self.host,
                    port=self.port,
                    clientId=self.client_id,
                    timeout=10
                )
            except Exception:
                continue  # try again after backoff

        logger.info("[IB UNDERLYING] Reconnected successfully")

        # Re-subscribe all symbols
        if self.symbols:
            await self.subscribe(self.symbols)

        # Notify orchestrator to RESUME
        if self.parent_orchestrator:
            try:
                self.parent_orchestrator.notify_underlying_recovered()
            except Exception:
                logger.exception("[IB] Failed to notify orchestrator (recovered)")

        self._reconnecting = False
