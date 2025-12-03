"""
MassiveOptionsWSAdapter — FINAL MUX-COMPATIBLE VERSION
------------------------------------------------------

Supports routing:
    • on_nbbo(callback)
    • on_quote(callback)
    • on_reconnect(callback)

Correct Massive syntax:
    Q.<OCC>

Massive Option Events:
    • ev = "NO"   → NBBO (bid/ask)
    • ev = "OQ"   → quote/greeks expansion
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

        # Callbacks
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
    # CALLBACK REGISTRATION
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

                    # Start router + watchdog
                    self._router_task = self.loop.create_task(self._router())
                    self._heartbeat_task = self.loop.create_task(self._heartbeat_watchdog())

                    logger.info("Massive OPTIONS WS connected ✓")

                    # Notify listeners
                    for cb in self._reconnect_handlers:
                        self.loop.create_task(cb())

                    return

                except Exception as e:
                    logger.error(f"[OPTIONS] WS connect failed: {e}")
                    await asyncio.sleep(self._retry)
                    self._retry = min(self._retry * 2, self._max_retry)

    # =============================================================
    async def subscribe_contracts(self, occ_codes: List[str]):
        """
        Correct syntax per Massive:
            Q.<OCC>
        """
        if not self.ws:
            await self.connect()

        BATCH = 4
        for i in range(0, len(occ_codes), BATCH):
            batch = occ_codes[i:i + BATCH]
            syms = [f"Q.{sym}" for sym in batch]

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
    async def _router(self):
        """Primary NBBO/quote event dispatcher."""
        try:
            async for raw in self.ws:
                now = time.time()
                self._last_heartbeat = now

                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                # Massive sometimes batches events
                events = msg if isinstance(msg, list) else [msg]

                for ev in events:
                    await self._dispatch(ev, now)

        except Exception as e:
            logger.error(f"[OPTIONS] Router crashed: {e}")

        finally:
            logger.warning("[OPTIONS] Router ending — reconnecting…")
            await self.connect()

    # =============================================================
    async def _dispatch(self, event: Dict[str, Any], ts: float):
        ev = event.get("ev")

        # NBBO or quote/greeks
        if ev not in ("NO", "OQ"):
            return

        occ = event.get("sym", "")
        if not occ.startswith("O:") or len(occ) < 20:
            return

        # OCC decode: O:SPY251205C00480000
        symbol = occ[2:5]
        right = occ[11]
        try:
            strike = int(occ[12:]) / 1000.0
        except:
            strike = None

        normalized = {
            "symbol": symbol,
            "contract": occ,
            "strike": strike,
            "right": right,
            "bid": event.get("bp"),
            "ask": event.get("ap"),
            "bid_size": event.get("bs"),
            "ask_size": event.get("as"),
            "_recv_ts": ts,

            # greeks
            "iv": event.get("iv"),
            "delta": event.get("delta"),
            "gamma": event.get("gamma"),
            "theta": event.get("theta"),
            "vega": event.get("vega"),

            # market stats
            "volume": event.get("vol"),
            "open_interest": event.get("oi"),

            "ev": ev,
        }

        # DEBUG: uncomment only for development
        # print(">> NBBO:", normalized)

        # NBBO callback
        for cb in self._nbbo_handlers:
            self.loop.create_task(cb(normalized))

        # Quote callback (rarely used)
        if ev == "OQ":
            for cb in self._quote_handlers:
                self.loop.create_task(cb(normalized))

    # =============================================================
    async def _heartbeat_watchdog(self):
        """Reconnects if feed stalls."""
        try:
            while True:
                await asyncio.sleep(5)
                if time.time() - self._last_heartbeat > 15:
                    logger.error("[OPTIONS] Heartbeat stale — reconnecting")
                    await self.connect()
        except asyncio.CancelledError:
            pass

    # =============================================================
    async def close(self):
        """Clean shutdown."""
        if self.ws:
            await self.ws.close()
        if self._router_task:
            self._router_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        logger.info("[OPTIONS] Massive WS closed")

