"""
MassiveOptionsWSAdapter v5.2 — FULLY CORRECTED
---------------------------------------------
✔ Deterministic async lifecycle
✔ All background tasks tracked & cancelled
✔ Clean Ctrl+C shutdown
✔ No orphan WS readers
✔ No post-shutdown NBBO prints
✔ SIDE-EFFECT-FREE parent assignment
"""

import asyncio
import json
import logging
import os
import time
import contextlib
from typing import Callable, List, Optional, Set

import websockets

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

WS_ENDPOINT = "wss://socket.massive.com/options"
_SUB_PACE_SEC = 0.15
_PING_INTERVAL = 15
_RECONNECT_BACKOFF = 2.5


class MassiveOptionsWSAdapter:
    @classmethod
    def from_env(cls):
        api_key = os.getenv("MASSIVE_API_KEY")
        endpoint = os.getenv("MASSIVE_WS_OPTIONS_URL", WS_ENDPOINT)
        if not api_key:
            raise RuntimeError("Environment variable MASSIVE_API_KEY is not set.")
        inst = cls(api_key=api_key)
        inst.endpoint = endpoint
        return inst

    # --------------------------------------------------------------
    def __init__(self, api_key: Optional[str] = None, loop=None):
        self.api_key = api_key or os.getenv("MASSIVE_API_KEY")
        if not self.api_key:
            raise RuntimeError("Missing MASSIVE_API_KEY")

        self.loop = loop or asyncio.get_event_loop()
        self.endpoint = WS_ENDPOINT

        # WS + lifecycle
        self.ws = None
        self._running = False

        # callbacks + topics
        self._on_option_cbs: List[Callable] = []
        self._target_topics: List[str] = []

        # orchestrator (private, set via set_parent())
        self._parent = None

        # TASK TRACKING (CRITICAL)
        self._router_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._bg_tasks: Set[asyncio.Task] = set()

    # --------------------------------------------------------------
    def set_parent(self, orch):
        """Explicit parent assignment with NO side effects."""
        self._parent = orch

    @property
    def parent_orchestrator(self):
        """Read-only access to parent."""
        return self._parent

    # --------------------------------------------------------------
    def on_option(self, cb):
        """Store callback reference only - no work."""
        self._on_option_cbs.append(cb)

    # --------------------------------------------------------------
    async def connect(self):
        self._running = True
        self._router_task = self.loop.create_task(self._router())
        logger.info("[OPTIONS] Connecting → %s", self.endpoint)

    # --------------------------------------------------------------
    async def shutdown(self):
        logger.info("[OPTIONS] Shutdown requested")
        self._running = False

        # Cancel router
        if self._router_task:
            self._router_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._router_task

        # Cancel ping
        if self._ping_task:
            self._ping_task.cancel()

        # Cancel all background tasks
        for t in list(self._bg_tasks):
            t.cancel()
        self._bg_tasks.clear()

        # Close websocket
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()

        logger.info("[OPTIONS] Shutdown complete")

    # Backward compatibility
    async def close(self):
        await self.shutdown()

    # --------------------------------------------------------------
    async def set_occ_subscriptions(self, occ_codes):
        topics = []
        for c in occ_codes:
            s = str(c)
            if not s.startswith("Q.O:"):
                s = "Q.O:" + s
            topics.append(s)

        self._target_topics = topics

        print("\n===== DEBUG OCC SUBSCRIPTIONS =====")
        for t in topics:
            print(t)
        print("===== END OCC SUBSCRIPTIONS =====\n")

        if self.ws:
            await self._subscribe_current_topics()

        return True

    # --------------------------------------------------------------
    async def _router(self):
        backoff = _RECONNECT_BACKOFF

        while self._running:
            try:
                async with websockets.connect(self.endpoint, ping_interval=None) as ws:
                    self.ws = ws
                    logger.info("[OPTIONS] Connected ✓")

                    await self._send({"action": "auth", "params": self.api_key})
                    await self._subscribe_current_topics()

                    self._ping_task = self.loop.create_task(self._pinger())

                    async for raw in ws:
                        if not self._running:
                            break

                        try:
                            msgs = json.loads(raw)
                        except Exception:
                            continue

                        if isinstance(msgs, dict):
                            msgs = [msgs]

                        for msg in msgs:
                            if msg.get("ev") == "Q":
                                self._handle_nbbo_message(msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[OPTIONS] Router disconnected: %s", e)

            await asyncio.sleep(backoff)

    # --------------------------------------------------------------
    async def _pinger(self):
        try:
            while self._running and self.ws:
                await asyncio.sleep(_PING_INTERVAL)
                await self._send({"action": "ping"})
        except asyncio.CancelledError:
            return

    async def _send(self, obj):
        if self.ws:
            await self.ws.send(json.dumps(obj))

    # --------------------------------------------------------------
    async def _subscribe_current_topics(self):
        if not self._target_topics:
            return

        logger.info("[OPTIONS] Subscribing to %d topics", len(self._target_topics))
        for topic in self._target_topics:
            await self._send({"action": "subscribe", "params": topic})
            print("[OPTIONS] Subscribed →", topic)
            await asyncio.sleep(_SUB_PACE_SEC)

    # --------------------------------------------------------------
    def _handle_nbbo_message(self, msg):
        try:
            raw_sym = msg.get("sym")
            if not raw_sym or not raw_sym.startswith("O:"):
                return

            occ = raw_sym[2:]
            root = self._parse_root_from_occ(occ)
            if not root:
                return

            bp = msg.get("bp")
            ap = msg.get("ap")
            if bp is None or ap is None:
                return

            ts_recv = time.time()

            event = {
                "ev": "Q",
                "symbol": root,
                "contract": occ,
                "bid": float(bp),
                "ask": float(ap),
                "bs": msg.get("bs"),
                "as": msg.get("as"),
                "t": msg.get("t"),
                "_recv_ts": ts_recv,
            }

            orch = self.parent_orchestrator
            if orch and orch.freshness and root in orch.freshness:
                orch.freshness[root].update(int(ts_recv * 1000))

            if orch is None or not hasattr(orch, "greek_injector"):
                self._fanout(event)
                return

            injector = orch.greek_injector

            async def _hydrate_and_forward(evt):
                try:
                    enriched = await injector.enrich(evt)
                    self._fanout(enriched)
                except Exception:
                    self._fanout(evt)

            t = self.loop.create_task(_hydrate_and_forward(event))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)

        except Exception:
            logger.exception("[OPTIONS] NBBO handler failed")

    # --------------------------------------------------------------
    def _fanout(self, event):
        for cb in self._on_option_cbs:
            t = self.loop.create_task(self._safe_cb(cb, event))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)

    async def _safe_cb(self, cb, event):
        try:
            if asyncio.iscoroutinefunction(cb):
                await cb(event)
            else:
                cb(event)
        except Exception:
            logger.exception("[OPTIONS] Option callback failed")

    @staticmethod
    def _parse_root_from_occ(occ):
        for i, ch in enumerate(occ):
            if ch.isdigit():
                return occ[:i]
        return None