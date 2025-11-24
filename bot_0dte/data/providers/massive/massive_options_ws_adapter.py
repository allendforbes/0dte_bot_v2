"""
MassiveOptionsWSAdapter — Options WebSocket Adapter

Responsibilities:
    • Connect to Massive.com options feed
    • Authenticate
    • Subscribe to option contracts (O:SPY241122C00450000, etc.)
    • Handle NBBO events (fast)
    • Handle Quote/Greek events (slower)
    • Normalize events to canonical format
    • Dispatch to registered callbacks
    • Auto-reconnect with backoff
    • Heartbeat watchdog
"""

import os
import json
import time
import asyncio
import logging
import websockets
from typing import Dict, Any, List, Callable

logger = logging.getLogger(__name__)


class MassiveOptionsWSAdapter:
    """
    Massive.com Options Adapter (NBBO + Quotes/Greeks)
    """

    WS_URL = "wss://socket.massive.com/options"

    def __init__(self, api_key: str, loop=None):
        self.api_key = api_key
        self.loop = loop or asyncio.get_event_loop()

        self.ws = None
        self._connected = asyncio.Event()

        # Callbacks
        self._nbbo_handlers: List[Callable] = []
        self._quote_handlers: List[Callable] = []

        # Health monitoring
        self._last_heartbeat = time.time()
        self._heartbeat_task = None
        self._router_task = None
        self._reconnect_lock = asyncio.Lock()

        # Backoff
        self._retry = 1
        self._max_retry = 30

    # ---------------------------------------------------------------------
    @classmethod
    def from_env(cls, loop=None):
        api_key = os.getenv("MASSIVE_API_KEY")
        if not api_key:
            raise RuntimeError("Missing MASSIVE_API_KEY in environment")
        return cls(api_key, loop=loop)

    # ---------------------------------------------------------------------
    # Public registrations
    # ---------------------------------------------------------------------
    def on_nbbo(self, cb: Callable):
        self._nbbo_handlers.append(cb)

    def on_quote(self, cb: Callable):
        self._quote_handlers.append(cb)

    # ---------------------------------------------------------------------
    # Connection
    # ---------------------------------------------------------------------
    async def connect(self):
        async with self._reconnect_lock:
            while True:
                try:
                    logger.info("Connecting to Massive OPTIONS feed...")

                    self.ws = await websockets.connect(
                        self.WS_URL,
                        ping_interval=20,
                        ping_timeout=10,
                    )

                    # Auth
                    await self.ws.send(
                        json.dumps({"action": "auth", "params": self.api_key})
                    )

                    logger.info("Auth message sent to Massive OPTIONS")

                    # Start router + heartbeat
                    self._connected.set()
                    self._last_heartbeat = time.time()
                    self._retry = 1

                    self._router_task = self.loop.create_task(self._router())
                    self._heartbeat_task = self.loop.create_task(
                        self._heartbeat_watchdog()
                    )

                    logger.info("Massive OPTIONS WebSocket connected")
                    return

                except Exception as e:
                    logger.error(f"[OPTIONS] WS connect failed: {e}")
                    await asyncio.sleep(self._retry)
                    self._retry = min(self._retry * 2, self._max_retry)

    # ---------------------------------------------------------------------
    async def subscribe_contracts(self, occ_codes: List[str]):
        """
        Subscribe to option contracts.

        Args:
            occ_codes: List of OCC codes (e.g., ["O:SPY241122C00445000"])
        """
        if not self.ws:
            await self.connect()

        sub_msg = {"action": "subscribe", "params": ",".join(occ_codes)}

        try:
            await self.ws.send(json.dumps(sub_msg))
            logger.info(f"[OPTIONS] Subscribed to {len(occ_codes)} contracts")
        except Exception:
            logger.warning("[OPTIONS] Send failed — reconnecting...")
            await self.connect()

    # ---------------------------------------------------------------------
    async def _router(self):
        """Route NBBO + Quote/Greeks events."""
        try:
            async for raw in self.ws:
                now = time.time()
                self._last_heartbeat = now

                msgs = json.loads(raw)
                if not isinstance(msgs, list):
                    msgs = [msgs]

                for event in msgs:
                    await self._dispatch(event, now)

        except Exception as e:
            logger.error(f"[OPTIONS] Router crashed: {e}")
        finally:
            logger.warning("[OPTIONS] Router ending — reconnecting")
            await self.connect()

    async def _dispatch(self, event: Dict[str, Any], recv_ts: float):
        """
        Normalize and dispatch option events.

        Normalized output:
        {
            "symbol": str (underlying),
            "contract": str (OCC code),
            "strike": float,
            "right": "C" | "P",
            "bid": float,
            "ask": float,
            "delta": float | None,
            "gamma": float | None,
            "_recv_ts": float
        }
        """
        ev = event.get("ev")

        # NBBO
        if ev == "NO":  # NBBO Options
            normalized = {
                "symbol": event.get("underlying", ""),
                "contract": event.get("sym", ""),
                "strike": event.get("strike", 0.0),
                "right": event.get("right", ""),
                "bid": event.get("bid", 0.0),
                "ask": event.get("ask", 0.0),
                "_recv_ts": recv_ts,
            }

            for cb in self._nbbo_handlers:
                self.loop.create_task(cb(normalized))

        # Quotes / Greeks
        elif ev == "OQ":  # Option Quotes w/ Greeks
            normalized = {
                "symbol": event.get("underlying", ""),
                "contract": event.get("sym", ""),
                "strike": event.get("strike", 0.0),
                "right": event.get("right", ""),
                "bid": event.get("bid", 0.0),
                "ask": event.get("ask", 0.0),
                "delta": event.get("delta"),
                "gamma": event.get("gamma"),
                "theta": event.get("theta"),
                "vega": event.get("vega"),
                "iv": event.get("iv"),
                "_recv_ts": recv_ts,
            }

            for cb in self._quote_handlers:
                self.loop.create_task(cb(normalized))

    # ---------------------------------------------------------------------
    async def _heartbeat_watchdog(self):
        try:
            while True:
                await asyncio.sleep(5)
                if time.time() - self._last_heartbeat > 10:
                    logger.error("[OPTIONS] Heartbeat stale — reconnecting")
                    await self.connect()
        except asyncio.CancelledError:
            pass

    # ---------------------------------------------------------------------
    async def close(self):
        if self.ws:
            await self.ws.close()

        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        if self._router_task:
            self._router_task.cancel()

        logger.info("MassiveOptionsWSAdapter closed")
