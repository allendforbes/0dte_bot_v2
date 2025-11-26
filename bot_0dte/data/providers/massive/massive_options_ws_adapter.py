"""
MassiveOptionsWSAdapter — Options WebSocket Adapter
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

        # Health
        self._last_heartbeat = time.time()
        self._router_task = None
        self._heartbeat_task = None
        self._reconnect_lock = asyncio.Lock()

        # Backoff
        self._retry = 1
        self._max_retry = 20

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, loop=None):
        api_key = os.getenv("MASSIVE_API_KEY")
        if not api_key:
            raise RuntimeError("Missing MASSIVE_API_KEY in environment")
        return cls(api_key, loop=loop)

    # ------------------------------------------------------------------
    def on_nbbo(self, cb: Callable):
        self._nbbo_handlers.append(cb)

    def on_quote(self, cb: Callable):
        self._quote_handlers.append(cb)

    # ------------------------------------------------------------------
    async def connect(self):
        """
        Connect to Massive with exponential backoff and proper auth.
        """
        async with self._reconnect_lock:
            while True:
                try:
                    logger.info("Connecting to Massive OPTIONS feed...")

                    self.ws = await websockets.connect(
                        self.WS_URL,
                        ping_interval=20,
                        ping_timeout=10,
                        max_queue=None,
                    )

                    # Authenticate
                    await self.ws.send(
                        json.dumps({"action": "auth", "params": self.api_key})
                    )
                    logger.info("Auth message sent to Massive OPTIONS")

                    self._connected.set()
                    self._last_heartbeat = time.time()
                    self._retry = 1

                    # Launch router + heartbeat watchdog
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

    # ------------------------------------------------------------------
    async def subscribe_contracts(self, occ_codes: List[str]):
        if not self.ws:
            await self.connect()

        # Massive limits: batch in groups of 3
        BATCH = 3
        for i in range(0, len(occ_codes), BATCH):
            batch = occ_codes[i : i + BATCH]

            sub_msg = {
                "type": "subscribe",
                "channels": [
                    {"name": "options", "symbols": batch}
                ]
            }

            try:
                await self.ws.send(json.dumps(sub_msg))
                logger.info(f"[OPTIONS] Subscribed to batch of {len(batch)} contracts")
            except Exception:
                logger.warning("[OPTIONS] Send failed — reconnecting...")
                await self.connect()

            # Safety spacing
            await asyncio.sleep(0.05)  # 50ms pause

    # ------------------------------------------------------------------
    async def _router(self):
        """
        Route NBBO + Greeks events.
        """
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

    # ------------------------------------------------------------------
    async def _dispatch(self, event: Dict[str, Any], recv_ts: float):
        ev = event.get("ev")

        # NBBO
        if ev == "NO":
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

        # Quotes/Greeks
        elif ev == "OQ":
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

    # ------------------------------------------------------------------
    async def _heartbeat_watchdog(self):
        """
        Detect stale feed and reconnect.
        """
        try:
            while True:
                await asyncio.sleep(5)
                if time.time() - self._last_heartbeat > 15:
                    logger.error("[OPTIONS] Heartbeat stale — reconnecting")
                    await self.connect()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    async def close(self):
        if self.ws:
            await self.ws.close()

        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        if self._router_task:
            self._router_task.cancel()

        logger.info("MassiveOptionsWSAdapter closed")
