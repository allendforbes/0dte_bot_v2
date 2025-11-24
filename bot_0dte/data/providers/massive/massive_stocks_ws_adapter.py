"""
MassiveStocksWSAdapter — Stocks WebSocket Adapter

Responsibilities:
    • Connect to Massive.com stocks feed
    • Authenticate
    • Subscribe to underlyings (Q.SPY, Q.QQQ, etc.)
    • Normalize events to canonical format
    • Dispatch to registered callbacks
    • Auto-reconnect with backoff
    • Heartbeat watchdog
"""

import os
import json
import asyncio
import logging
import websockets
import time
from typing import Callable, Dict, List, Any

logger = logging.getLogger(__name__)


class MassiveStocksWSAdapter:
    """
    Low-latency WebSocket adapter for Massive.com STOCKS feed.
    """

    WS_URL = "wss://socket.massive.com/stocks"

    def __init__(self, api_key: str, loop=None):
        self.api_key = api_key
        self.loop = loop or asyncio.get_event_loop()

        self.ws = None
        self._connected = asyncio.Event()

        self._underlying_handlers: List[Callable] = []

        # health
        self._last_heartbeat = time.time()
        self._heartbeat_task = None
        self._router_task = None
        self._reconnect_lock = asyncio.Lock()

        # backoff
        self._retry_interval = 1
        self._retry_max = 20

    # ------------------------------------------------------------------
    # Factory from environment variables
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, loop=None):
        api_key = os.getenv("MASSIVE_API_KEY")
        if not api_key:
            raise RuntimeError("Missing MASSIVE_API_KEY in environment")
        return cls(api_key, loop=loop)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def connect(self):
        return await self._connect_ws()

    async def subscribe(self, symbols: List[str]):
        """
        Subscribe to underlying quotes.

        Format: { action: "subscribe", params: "Q.SPY,Q.QQQ" }
        """
        params = ",".join([f"Q.{s.upper()}" for s in symbols])
        await self._send({"action": "subscribe", "params": params})

    def on_underlying(self, cb: Callable):
        """Register callback for incoming underlying ticks."""
        self._underlying_handlers.append(cb)

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self._router_task:
            self._router_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

    # ------------------------------------------------------------------
    # Internal connect/auth loop
    # ------------------------------------------------------------------
    async def _connect_ws(self):
        async with self._reconnect_lock:
            while True:
                try:
                    logger.info("Connecting to Massive STOCKS WS...")

                    self.ws = await websockets.connect(
                        self.WS_URL,
                        ping_interval=20,
                        ping_timeout=10,
                    )

                    # AUTH
                    await self.ws.send(
                        json.dumps({"action": "auth", "params": self.api_key})
                    )

                    # Wait for auth success
                    auth_msg = json.loads(await self.ws.recv())
                    if auth_msg[0].get("status") != "auth_success":
                        raise RuntimeError("Massive auth failed")

                    self._connected.set()
                    self._last_heartbeat = time.time()
                    self._retry_interval = 1

                    logger.info("Massive STOCKS authenticated + connected")

                    # Start router + heartbeat
                    self._router_task = self.loop.create_task(self._router())
                    self._heartbeat_task = self.loop.create_task(
                        self._heartbeat_watchdog()
                    )
                    return

                except Exception as e:
                    logger.error(f"STOCKS WS connect failed: {e}")
                    await asyncio.sleep(self._retry_interval)
                    self._retry_interval = min(
                        self._retry_interval * 2, self._retry_max
                    )

    async def _send(self, payload: Dict[str, Any]):
        """Send safely with reconnect if broken."""
        if not self.ws:
            await self._connect_ws()

        try:
            await self.ws.send(json.dumps(payload))
        except Exception:
            await self._connect_ws()

    # ------------------------------------------------------------------
    # Router for all incoming messages
    # ------------------------------------------------------------------
    async def _router(self):
        try:
            async for msg in self.ws:
                now = time.time()
                self._last_heartbeat = now

                data = json.loads(msg)

                # Massive sends arrays of events
                if isinstance(data, list):
                    for event in data:
                        await self._dispatch(event, now)
                else:
                    await self._dispatch(data, now)

        except Exception as e:
            logger.error(f"STOCKS router stopped: {e}")

        finally:
            logger.warning("Stocks router ended — reconnecting")
            await self._connect_ws()

    async def _dispatch(self, event: Dict[str, Any], recv_ts: float):
        """
        Normalize and dispatch underlying tick.

        Massive format: event["ev"] == "Q", event["sym"] = ticker

        Normalized output:
        {
            "symbol": str,
            "price": float,
            "bid": float | None,
            "ask": float | None,
            "_recv_ts": float
        }
        """
        if event.get("ev") == "Q":
            raw_symbol = event.get("sym")
            if not raw_symbol:
                return

            # Normalize event format
            normalized = {
                "symbol": raw_symbol,
                "price": event.get("p", 0.0),  # Massive uses "p" for price
                "bid": event.get("bp"),  # bid price (optional)
                "ask": event.get("ap"),  # ask price (optional)
                "_recv_ts": recv_ts,
            }

            for cb in self._underlying_handlers:
                self.loop.create_task(cb(normalized))

    # ------------------------------------------------------------------
    # Heartbeat watchdog
    # ------------------------------------------------------------------
    async def _heartbeat_watchdog(self):
        try:
            while True:
                await asyncio.sleep(5)
                if time.time() - self._last_heartbeat > 10:
                    logger.error("Stocks WS heartbeat stale → reconnecting")
                    await self._connect_ws()
        except asyncio.CancelledError:
            pass
