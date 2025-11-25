"""
IBUnderlyingAdapter — IBKR Real-Time Underlying Streaming
"""

import asyncio
import time
import logging
from typing import Callable, List

from ib_insync import IB, Stock, Ticker

logger = logging.getLogger(__name__)


class IBUnderlyingAdapter:
    def __init__(self, host="127.0.0.1", port=4002, client_id=11, loop=None):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.loop = loop or asyncio.get_event_loop()

        self.ib = IB()
        self.symbols: List[str] = []
        self._connected = False
        self._contracts = {}
        self._tickers = {}
        self._handlers: List[Callable] = []
        self._watchdog_task = None

    def on_underlying(self, cb: Callable):
        self._handlers.append(cb)

    async def connect(self):
        logger.info(f"[IB UNDERLYING] Connecting to {self.host}:{self.port}...")
        try:
            await self.ib.connectAsync(
                host=self.host, port=self.port, clientId=self.client_id, timeout=15
            )
            self._connected = True
            self._watchdog_task = self.loop.create_task(self._connection_watchdog())
            logger.info("[IB UNDERLYING] Connected ✅")
        except Exception as e:
            logger.error(f"[IB UNDERLYING] Connection failed: {e}")
            raise

    async def subscribe(self, symbols: List[str]):
        if not self._connected:
            raise RuntimeError("Connect first before subscribing.")
        logger.info(f"[IB UNDERLYING] Subscribing: {symbols}")
        self.symbols = symbols
        for symbol in symbols:
            await self._subscribe_symbol(symbol)
        logger.info("[IB UNDERLYING] Subscriptions active")

    async def close(self):
        logger.info("[IB UNDERLYING] Closing...")
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self.ib.isConnected():
            self.ib.disconnect()
        self._connected = False
        logger.info("[IB UNDERLYING] Disconnected.")

    async def _subscribe_symbol(self, symbol: str):
        stock_contract = Stock(symbol, "SMART", "USD")
        qualified = await self.ib.qualifyContractsAsync(stock_contract)
        if not qualified:
            logger.error(f"[IB UNDERLYING] Failed to qualify {symbol}")
            return
        contract = qualified[0]
        self._contracts[symbol] = contract
        ticker = self.ib.reqMktData(
            contract, genericTickList="", snapshot=False, regulatorySnapshot=False
        )
        self._tickers[symbol] = ticker
        ticker.updateEvent += lambda t, sym=symbol: self._on_ticker_update(sym, t)
        logger.info(f"[IB UNDERLYING] Subscribed: {symbol}")

    def _on_ticker_update(self, symbol: str, ticker: Ticker):
        price = ticker.last if ticker.last and ticker.last > 0 else ticker.close
        bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
        ask = ticker.ask if ticker.ask and ticker.ask > 0 else None
        if not price or price <= 0:
            return
        event = {
            "symbol": symbol,
            "price": float(price),
            "bid": float(bid) if bid else None,
            "ask": float(ask) if ask else None,
            "_recv_ts": time.time(),
        }
        for cb in self._handlers:
            self.loop.create_task(cb(event))

    async def _connection_watchdog(self):
        try:
            while True:
                await asyncio.sleep(5)
                if not self.ib.isConnected():
                    logger.error("[IB UNDERLYING] Lost connection; reconnecting...")
                    await self._reconnect()
        except asyncio.CancelledError:
            pass

    async def _reconnect(self):
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
            await asyncio.sleep(1)
            await self.connect()
            if self.symbols:
                await self.subscribe(self.symbols)
            logger.info("[IB UNDERLYING] Reconnected successfully.")
        except Exception as e:
            logger.error(f"[IB UNDERLYING] Reconnect failed: {e}")
