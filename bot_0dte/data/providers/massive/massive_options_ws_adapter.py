"""
MassiveOptionsWSAdapter v5.1 — FINAL FIXED (Async Subscriptions)
---------------------------------------------------------------
- Async set_occ_subscriptions() (required by MassiveMux v3.5)
- Immediate application of new OCC topics when WS is live
- Correct Massive NBBO schema handling
- Compatible with ContractEngine refresh logic
"""

import asyncio
import json
import logging
import os
import time
from typing import Callable, List, Optional

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
        self.ws = None
        self._running = False

        self._on_option_cbs: List[Callable] = []
        self._target_topics: List[str] = []

        self.parent_orchestrator = None
        self.endpoint = WS_ENDPOINT

    # --------------------------------------------------------------
    def on_option(self, cb):
        self._on_option_cbs.append(cb)

    # --------------------------------------------------------------
    async def connect(self):
        self._running = True
        self.loop.create_task(self._router())
        logger.info("[OPTIONS] Connecting → %s", self.endpoint)

    async def close(self):
        self._running = False
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass

    # --------------------------------------------------------------
    # ASYNC — REQUIRED BY MassiveMux + ContractEngine
    # --------------------------------------------------------------
    async def set_occ_subscriptions(self, occ_codes):
        """
        Convert OCC codes → Massive WS topics and store them.

        If WS already connected → immediately subscribe.
        """
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

        # ⭐ CRITICAL FIX — apply subs immediately if WS is already connected
        if self.ws:
            await self._subscribe_current_topics()

        return True  # ⭐ REQUIRED so MassiveMux can safely `await` this


    # --------------------------------------------------------------
    async def _router(self):
        backoff = _RECONNECT_BACKOFF

        while self._running:
            try:
                async with websockets.connect(self.endpoint, ping_interval=None) as ws:
                    self.ws = ws
                    logger.info("[OPTIONS] Connected ✓")

                    await self._send({"action": "auth", "params": self.api_key})
                    logger.info("[OPTIONS] Auth sent")

                    await self._subscribe_current_topics()
                    ping_task = self.loop.create_task(self._pinger())

                    # ---------------- MAIN READ LOOP -----------------
                    async for raw in ws:
                        print("RAW WS MSG:", raw)  # DEBUG

                        try:
                            msgs = json.loads(raw)
                        except Exception:
                            logger.exception("[OPTIONS] JSON decode failed")
                            continue

                        if isinstance(msgs, dict):
                            msgs = [msgs]

                        for msg in msgs:
                            if msg.get("ev") == "Q":
                                self._handle_nbbo_message(msg)

                    ping_task.cancel()

            except Exception as e:
                logger.warning("[OPTIONS] Router disconnected: %s", e)

            await asyncio.sleep(backoff)

    # --------------------------------------------------------------
    async def _pinger(self):
        while self._running and self.ws:
            try:
                await asyncio.sleep(_PING_INTERVAL)
                await self._send({"action": "ping"})
            except:
                return

    async def _send(self, obj):
        if self.ws:
            await self.ws.send(json.dumps(obj))

    # --------------------------------------------------------------
    async def _subscribe_current_topics(self):
        if not self._target_topics:
            logger.warning("[OPTIONS] No OCC topics set.")
            return

        logger.info("[OPTIONS] Subscribing to %d topics", len(self._target_topics))

        for topic in self._target_topics:
            frame = {"action": "subscribe", "params": topic}
            await self._send(frame)
            print("[OPTIONS] Subscribed →", topic)
            await asyncio.sleep(_SUB_PACE_SEC)

    # --------------------------------------------------------------
    # NEW MASSIVE NBBO FORMAT
    # --------------------------------------------------------------
    def _handle_nbbo_message(self, msg):
        try:
            raw_sym = msg.get("sym")  # Example: "O:SPY251212C00500000"
            if not raw_sym or not raw_sym.startswith("O:"):
                return

            occ = raw_sym[2:]  # remove "O:"
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

            # Freshness update
            orch = self.parent_orchestrator
            if orch and orch.freshness and root in orch.freshness:
                orch.freshness[root].update(int(ts_recv * 1000))

            print("[WS → CALLBACK] NBBO event:", event)

            for cb in self._on_option_cbs:
                self.loop.create_task(self._safe_cb(cb, event))

        except Exception:
            logger.exception("[OPTIONS] NBBO handler failed")

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
