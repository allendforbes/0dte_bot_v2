"""
MassiveOptionsWSAdapter — FINAL WORKING VERSION
------------------------------------------------

Handles Massive OPTIONS WebSocket streaming.

NBBO events:
    ev = "Q"    (REAL bid/ask NBBO stream)
    ev = "OQ"   (greeks + extended quote)

Correct Massive subscription format:
    Q.O:<OCC_CODE>

OCC_CODE must NOT include "O:" — the adapter adds it.
Example final symbol:  Q.O:SPY251203C00682000
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
    WS_URL = "wss://socket.massive.com/options"

    def __init__(self, api_key: str, loop=None):
        self.api_key = api_key
        self.loop = loop or asyncio.get_event_loop()

        self.ws = None

        # Handlers registered by upper layers
        self._nbbo_handlers: List[Callable] = []
        self._quote_handlers: List[Callable] = []
        self._reconnect_handlers: List[Callable] = []

        # State
        self._connected = asyncio.Event()
        self._last_heartbeat = time.time()
        self._router_task = None
        self._heartbeat_task = None
        self._reconnect_lock = asyncio.Lock()

        # Reconnect backoff
        self._retry = 1
        self._max_retry = 20

    # =============================================================
    # REGISTRATION
    # =============================================================
    def on_nbbo(self, cb: Callable):
        self._nbbo_handlers.append(cb)

    def on_quote(self, cb: Callable):
        self._quote_handlers.append(cb)

    def on_reconnect(self, cb: Callable):
        self._reconnect_handlers.append(cb)

    # =============================================================
    @classmethod
    def from_env(cls, loop=None):
        key = os.getenv("MASSIVE_API_KEY")
        if not key:
            raise RuntimeError("Missing MASSIVE_API_KEY in environment")
        return cls(key, loop=loop)

    # =============================================================
    # CONNECTION
    # =============================================================
    async def connect(self):
        """Main connection loop with exponential backoff."""
        async with self._reconnect_lock:
            while True:
                try:
                    logger.info("Connecting to Massive OPTIONS WS...")

                    self.ws = await websockets.connect(
                        self.WS_URL,
                        ping_interval=20,
                        ping_timeout=10,
                    )

                    # Authenticate
                    await self.ws.send(json.dumps({
                        "action": "auth",
                        "params": self.api_key
                    }))
                    logger.info("Auth message sent to Massive OPTIONS")

                    self._connected.set()
                    self._last_heartbeat = time.time()
                    self._retry = 1

                    # Start tasks
                    self._router_task = self.loop.create_task(self._router())
                    self._heartbeat_task = self.loop.create_task(self._heartbeat_watchdog())

                    logger.info("Massive OPTIONS WS connected ✓")

                    # Notify reconnect listeners
                    for cb in self._reconnect_handlers:
                        self.loop.create_task(cb())

                    return

                except Exception as e:
                    logger.error(f"[OPTIONS] WS connect failed: {e}")
                    await asyncio.sleep(self._retry)
                    self._retry = min(self._retry * 2, self._max_retry)

    # =============================================================
    # SUBSCRIBE OCC CODES
    # =============================================================
    async def subscribe_contracts(self, occ_codes: List[str]):
        """
        Accepts PURE OCC codes like:
            SPY251203C00680000

        Sends:
            Q.O:<OCC>

        NEVER send "O:O:".
        """

        if not self.ws:
            await self.connect()

        BATCH = 4

        for i in range(0, len(occ_codes), BATCH):
            batch = occ_codes[i : i + BATCH]

            final_codes = []
            for occ in batch:
                if occ.startswith("O:"):
                    occ = occ[2:]     # remove accidental prefix
                final_codes.append(f"O:{occ}")  # EXACTLY ONE prefix

            syms = [f"Q.{c}" for c in final_codes]

            msg = json.dumps({
                "action": "subscribe",
                "params": ",".join(syms)
            })

            try:
                await self.ws.send(msg)
                logger.info(f"[OPTIONS] Subscribed to {syms}")
            except Exception as e:
                logger.error(f"[OPTIONS] subscribe failed ({e}) — reconnecting…")
                await self.connect()

            await asyncio.sleep(0.05)

    # =============================================================
    # ROUTER
    # =============================================================
    async def _router(self):
        """Reads all incoming WS messages and dispatches them."""
        try:
            async for raw in self.ws:
                now = time.time()
                self._last_heartbeat = now

                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                events = msg if isinstance(msg, list) else [msg]

                for ev in events:
                    await self._dispatch(ev, now)

        except Exception as e:
            logger.error(f"[OPTIONS] Router crashed: {e}")

        finally:
            logger.warning("[OPTIONS] Router ending — reconnecting…")
            await self.connect()

    # =============================================================
    # DISPATCH (NBBO + OQ)
    # =============================================================
    async def _dispatch(self, event: Dict[str, Any], ts: float):
        ev = event.get("ev")

        # --------------------------------------------------------
        # MASSIVE NBBO = ev "Q"
        # --------------------------------------------------------
        if ev == "Q":
            occ = event.get("sym", "")
            if not occ.startswith("O:"):
                return

            normalized = {
                "symbol": occ[2:5],
                "contract": occ,
                "bid": event.get("bp"),
                "ask": event.get("ap"),
                "bid_size": event.get("bs"),
                "ask_size": event.get("as"),
                "iv": event.get("iv"),
                "delta": event.get("delta"),
                "gamma": event.get("gamma"),
                "theta": event.get("theta"),
                "vega": event.get("vega"),
                "volume": event.get("vol"),
                "open_interest": event.get("oi"),
                "_recv_ts": ts,
                "ev": "Q",
            }

            for cb in self._nbbo_handlers:
                self.loop.create_task(cb(normalized))

            return

        # --------------------------------------------------------
        # MASSIVE OQ (extended quote)
        # --------------------------------------------------------
        if ev == "OQ":
            occ = event.get("sym", "")
            if not occ.startswith("O:"):
                return

            normalized = {
                "symbol": occ[2:5],
                "contract": occ,
                "bid": event.get("bp"),
                "ask": event.get("ap"),
                "bid_size": event.get("bs"),
                "ask_size": event.get("as"),
                "iv": event.get("iv"),
                "delta": event.get("delta"),
                "gamma": event.get("gamma"),
                "theta": event.get("theta"),
                "vega": event.get("vega"),
                "volume": event.get("vol"),
                "open_interest": event.get("oi"),
                "_recv_ts": ts,
                "ev": "OQ",
            }

            # NBBO listeners ALSO get OQ
            for cb in self._nbbo_handlers:
                self.loop.create_task(cb(normalized))

            # Specialized quote listeners
            for cb in self._quote_handlers:
                self.loop.create_task(cb(normalized))

            return

        # Ignore status packets / heartbeats / unknown events
        return

    # =============================================================
    # HEARTBEAT WATCHDOG
    # =============================================================
    async def _heartbeat_watchdog(self):
        try:
            while True:
                await asyncio.sleep(5)
                if time.time() - self._last_heartbeat > 15:
                    logger.error("[OPTIONS] Heartbeat stale — reconnecting")
                    await self.connect()
        except asyncio.CancelledError:
            pass

    # =============================================================
    # CLEAN SHUTDOWN
    # =============================================================
    async def close(self):
        if self.ws:
            await self.ws.close()
        if self._router_task:
            self._router_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        logger.info("[OPTIONS] Massive WS closed")
