"""
MassiveOptionsWSAdapter — FINAL MUX-COMPATIBLE VERSION
Supports routing:
    • on_nbbo(callback)
    • on_quote(callback)
    • on_reconnect(callback)
Handles correct Massive format:
    Q.O:<OCC>
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

        # REQUIRED by MUX:
        self._nbbo_handlers: List[Callable] = []
        self._quote_handlers: List[Callable] = []
        self._reconnect_handlers: List[Callable] = []

        self._connected = asyncio.Event()
        self._last_heartbeat = time.time()
        self._router_task = None
        self._heartbeat_task = None
        self._reconnect_lock = asyncio.Lock()

        # Backoff
        self._retry = 1
        self._max_retry = 20

    # ----------------------------------------------------------------------
    # REQUIRED PUBLIC API
    # ----------------------------------------------------------------------
    def on_nbbo(self, cb: Callable):
        """Register NBBO handler (required by MassiveMux)."""
        self._nbbo_handlers.append(cb)

    def on_quote(self, cb: Callable):
        """Register quote/Greeks handler."""
        self._quote_handlers.append(cb)

    def on_reconnect(self, cb: Callable):
        """Register reconnect callback (ContractEngine uses this)."""
        self._reconnect_handlers.append(cb)

    # ----------------------------------------------------------------------
    @classmethod
    def from_env(cls, loop=None):
        key = os.getenv("MASSIVE_API_KEY")
        if not key:
            raise RuntimeError("Missing MASSIVE_API_KEY in env")
        return cls(key, loop=loop)

    # ----------------------------------------------------------------------
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

                    await self.ws.send(json.dumps({
                        "action": "auth",
                        "params": self.api_key
                    }))

                    logger.info("Auth message sent to Massive OPTIONS")

                    self._connected.set()
                    self._last_heartbeat = time.time()
                    self._retry = 1

                    # start router + heartbeat
                    self._router_task = self.loop.create_task(self._router())
                    self._heartbeat_task = self.loop.create_task(self._heartbeat_watchdog())

                    logger.info("Massive OPTIONS WebSocket connected")

                    # FIRE RECONNECT HANDLERS
                    for cb in self._reconnect_handlers:
                        self.loop.create_task(cb())

                    return

                except Exception as e:
                    logger.error(f"[OPTIONS] WS connect failed: {e}")
                    await asyncio.sleep(self._retry)
                    self._retry = min(self._retry * 2, self._max_retry)

    # ----------------------------------------------------------------------
    async def subscribe_contracts(self, occ_codes: List[str]):
        if not self.ws:
            await self.connect()

        BATCH = 3
        for i in range(0, len(occ_codes), BATCH):
            batch = occ_codes[i : i + BATCH]

            # Massive requires Q.O:<contract>
            syms = [f"Q.{sym}" for sym in batch]

            msg = json.dumps({
                "action": "subscribe",
                "params": ",".join(syms)
            })

            try:
                await self.ws.send(msg)
                logger.info(f"[OPTIONS] Subscribed → {syms}")

            except Exception as e:
                logger.warning(f"[OPTIONS] Send failed ({e}) — reconnecting...")
                await self.connect()

            await asyncio.sleep(0.05)

    # ----------------------------------------------------------------------
    async def _router(self):
        try:
            async for raw in self.ws:
                now = time.time()
                self._last_heartbeat = now

                data = json.loads(raw)
                if not isinstance(data, list):
                    data = [data]

                for event in data:
                    await self._dispatch(event, now)

        except Exception as e:
            logger.error(f"[OPTIONS] Router crashed: {e}")
        finally:
            logger.warning("[OPTIONS] Router ending — reconnecting")
            await self.connect()

    # ----------------------------------------------------------------------
    async def _dispatch(self, event: Dict[str, Any], recv_ts: float):

        if event.get("ev") != "Q":
            return

        contract = event.get("sym", "")

        und = ""
        right = None
        strike = None

        try:
            # Massive sends a 20-character OCC-style string
            if contract.startswith("O:") and len(contract) >= 19:
                und = contract[2:5]            # SPY / QQQ
                right = contract[11]           # char at index 11
                strike_raw = contract[12:]     # remainder → "00683000"
                strike = int(strike_raw) / 1000.0
        except Exception as e:
            print("OCC DECODE ERROR:", e, contract)

        normalized = {
            "symbol": und,
            "contract": contract,
            "strike": strike,
            "right": right,
            "bid": event.get("bp"),
            "ask": event.get("ap"),
            "bid_size": event.get("bs"),
            "ask_size": event.get("as"),
            "_recv_ts": recv_ts,
        }

        print(">>> NORMALIZED:", normalized)

        for cb in self._nbbo_handlers:
            self.loop.create_task(cb(normalized))



    # ----------------------------------------------------------------------
    async def _heartbeat_watchdog(self):
        try:
            while True:
                await asyncio.sleep(5)
                if time.time() - self._last_heartbeat > 15:
                    logger.error("[OPTIONS] Heartbeat stale — reconnecting")
                    await self.connect()
        except asyncio.CancelledError:
            pass

    # ----------------------------------------------------------------------
    async def close(self):
        if self.ws:
            await self.ws.close()

        if self._router_task:
            self._router_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        logger.info("MassiveOptionsWSAdapter closed")
