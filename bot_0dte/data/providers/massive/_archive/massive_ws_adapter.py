# bot_0dte/data/adapters/massive_ws_adapter.py

import os
import json
import asyncio
import logging
import websockets
import time
from typing import Callable, List, Dict, Any

logger = logging.getLogger(__name__)


class MassiveWSAdapter:
    """
    Unified Massive.com WebSocket adapter
    - real + mock modes
    - reconnect logic with bounded backoff
    - heartbeat watchdog
    - underlying + option NBBO routing
    - timestamp injection for latency analytics
    """

    MASSIVE_WS_URL = "wss://stream.massive.app/v1/options"  # replace if needed

    def __init__(self, api_key: str, api_secret: str, loop=None, mock=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.loop = loop or asyncio.get_event_loop()
        self.mock = mock

        self.ws = None
        self._connected = asyncio.Event()

        # callbacks
        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        # health
        self._last_heartbeat = time.time()
        self._heartbeat_task = None
        self._router_task = None
        self._reconnect_lock = asyncio.Lock()

        # adaptive backoff
        self._retry_interval = 1
        self._retry_max = 30

    # ----------------------------------------------------------------------
    # Factory
    # ----------------------------------------------------------------------
    @classmethod
    def from_env(cls, loop=None):
        api_key = os.getenv("MASSIVE_API_KEY")
        api_secret = os.getenv("MASSIVE_API_SECRET")
        mock = not (api_key and api_secret)
        if mock:
            logger.warning(
                "MassiveWSAdapter: running in MOCK MODE (missing credentials)"
            )
        return cls(api_key, api_secret, loop=loop, mock=mock)

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------
    async def connect(self):
        if self.mock:
            return await self._start_mock_streams()
        return await self._connect_real_ws()

    async def subscribe_underlyings(self, symbols: List[str]):
        if self.mock:
            return
        await self._send(
            {"action": "subscribe", "channel": "underlyings", "symbols": symbols}
        )

    async def subscribe_options(self, contracts: List[str]):
        if self.mock:
            return
        await self._send(
            {"action": "subscribe", "channel": "options_nbbo", "contracts": contracts}
        )

    def on_underlying(self, cb: Callable):
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        self._option_handlers.append(cb)

    async def close(self):
        if self.ws:
            await self.ws.close()

        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        if self._router_task:
            self._router_task.cancel()

        logger.info("MassiveWSAdapter closed")

    # ----------------------------------------------------------------------
    # Real WS Mode
    # ----------------------------------------------------------------------
    async def _connect_real_ws(self):
        async with self._reconnect_lock:
            while True:
                try:
                    logger.info("Connecting to Massive WS...")
                    self.ws = await websockets.connect(
                        self.MASSIVE_WS_URL,
                        extra_headers={
                            "x-api-key": self.api_key,
                            "x-api-secret": self.api_secret,
                        },
                        ping_interval=20,
                        ping_timeout=10,
                    )

                    self._connected.set()
                    self._last_heartbeat = time.time()
                    self._retry_interval = 1

                    logger.info("Massive WS connected")

                    # start router + watchdog
                    self._router_task = self.loop.create_task(self._router())
                    self._heartbeat_task = self.loop.create_task(
                        self._heartbeat_watchdog()
                    )

                    return

                except Exception as e:
                    logger.error(f"WS connect failed: {e}")
                    await asyncio.sleep(self._retry_interval)
                    self._retry_interval = min(
                        self._retry_interval * 2, self._retry_max
                    )

    async def _send(self, payload: Dict[str, Any]):
        """Safe send with reconnect if needed."""
        if not self.ws:
            await self._connect_real_ws()
        try:
            await self.ws.send(json.dumps(payload))
        except Exception:
            logger.warning("WS send failed → reconnecting")
            await self._connect_real_ws()

    async def _router(self):
        """Route Massive messages to handlers."""
        try:
            async for msg in self.ws:
                now = time.time()
                self._last_heartbeat = now

                data = json.loads(msg)

                # Inject source timestamp for latency tracking
                data["_recv_ts"] = now

                channel = data.get("channel")
                if channel == "underlyings":
                    for cb in self._underlying_handlers:
                        self.loop.create_task(cb(data))

                elif channel == "options_nbbo":
                    for cb in self._option_handlers:
                        self.loop.create_task(cb(data))

        except Exception as e:
            logger.error(f"Router stopped: {e}")
        finally:
            logger.warning("Router ended → reconnecting")
            await self._connect_real_ws()

    async def _heartbeat_watchdog(self):
        """Kill-switch for stale streams."""
        try:
            while True:
                await asyncio.sleep(5)
                if time.time() - self._last_heartbeat > 10:
                    logger.error("Massive WS stale heartbeat → reconnecting")
                    await self._connect_real_ws()
        except asyncio.CancelledError:
            pass

    # ----------------------------------------------------------------------
    # Mock Mode (synthetic ticks)
    # ----------------------------------------------------------------------
    async def _start_mock_streams(self):
        logger.info("MassiveWSAdapter mock stream starting")

        async def _mock_loop():
            while True:
                ts = time.time()
                sample_underlying = {
                    "channel": "underlyings",
                    "symbol": "SPX",
                    "price": 5000 + (ts % 5),
                    "_recv_ts": ts,
                }
                for cb in self._underlying_handlers:
                    self.loop.create_task(cb(sample_underlying))

                sample_option = {
                    "channel": "options_nbbo",
                    "contract": "SPXW-20240221-5000-C",
                    "bid": 1.20,
                    "ask": 1.30,
                    "_recv_ts": ts,
                }
                for cb in self._option_handlers:
                    self.loop.create_task(cb(sample_option))

                await asyncio.sleep(0.05)  # ~20 ticks/sec

        self._router_task = self.loop.create_task(_mock_loop())
        return
