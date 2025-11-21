# bot_0dte/data/adapters/massive_options_ws_adapter.py

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
    --------------------------------------------------
    Responsibilities:
        • Connect to real-time options websocket
        • Authenticate with API key
        • Subscribe to option contracts (O:<OCC_CODE>)
        • Handle NBBO events (fast)
        • Handle Quote/Greek events (slower)
        • Reconnect on failure
        • Heartbeat watchdog
        • Callback system for orchestrator/chain aggregator

    All events include:
        "_recv_ts" -- precise receipt timestamp for latency analytics
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
        occ_codes example:
            ["O:SPY241122C00445000", "O:SPY241122P00445000"]
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
                    event["_recv_ts"] = now

                    ev = event.get("ev")

                    # NBBO
                    if ev == "NO":  # NBBO Options
                        for cb in self._nbbo_handlers:
                            self.loop.create_task(cb(event))

                    # Quotes / Greeks
                    elif ev == "OQ":  # Option Quotes w/ Greeks
                        for cb in self._quote_handlers:
                            self.loop.create_task(cb(event))

        except Exception as e:
            logger.error(f"[OPTIONS] Router crashed: {e}")
        finally:
            logger.warning("[OPTIONS] Router ending — reconnecting")
            await self.connect()

    # ---------------------------------------------------------------------
    async def _heartbeat_watchdog(self):
        while True:
            await asyncio.sleep(5)
            if time.time() - self._last_heartbeat > 10:
                logger.error("[OPTIONS] Heartbeat stale — reconnecting")
                await self.connect()

    # ---------------------------------------------------------------------
    async def close(self):
        if self.ws:
            await self.ws.close()

        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        if self._router_task:
            self._router_task.cancel()

        logger.info("MassiveOptionsWSAdapter closed")
