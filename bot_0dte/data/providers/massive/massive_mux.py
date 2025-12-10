"""
MassiveMux v3.5 — Underlying Router FIXED
-----------------------------------------
✓ Underlying ticks → engine.on_underlying → orchestrator.on_underlying
✓ Proper await set_occ_subscriptions
✓ Underlying warmup supported
✓ Freshness trackers preserved
"""

import asyncio
import logging
from typing import Dict, List

from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine
from bot_0dte.infra.freshness import FreshnessTracker

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MassiveMux:
    def __init__(self, options_ws=None, ib_underlying=None, loop=None, **kwargs):

        if options_ws is None:
            options_ws = kwargs.pop("options_adapter", None)
        if options_ws is None:
            raise TypeError("MassiveMux requires 'options_ws'.")

        self.options = options_ws
        self.ib = ib_underlying
        self.loop = loop or asyncio.get_event_loop()

        self.parent_orchestrator = None
        self._on_option_cb = None
        self._on_underlying_cb = None

        self.freshness: Dict[str, FreshnessTracker] = {}
        self.engines: Dict[str, MassiveContractEngine] = {}

    # ---------------------------------------------------------
    def on_option(self, cb):
        self._on_option_cb = cb
        self.options.on_option(cb)

    # ---------------------------------------------------------
    def on_underlying(self, cb):
        self._on_underlying_cb = cb
        if self.ib:
            # underlying now routed through mux
            self.ib.on_underlying(self._handle_underlying_event)

    # ---------------------------------------------------------
    async def _handle_underlying_event(self, event):
        sym = event.get("symbol")

        # ---- 1) engine receives underlying ticks ----
        eng = self.engines.get(sym)
        if eng:
            try:
                await eng.on_underlying(event)
            except Exception:
                logger.exception("[MUX] Engine underlying handler failed")

        # ---- 2) orchestrator receives underlying ticks ----
        if self._on_underlying_cb:
            try:
                await self._on_underlying_cb(event)
            except Exception:
                logger.exception("[MUX] Orchestrator underlying handler failed")

    # ---------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        logger.info("[MUX] Connecting w/ symbols: %s", symbols)

        # Build engines + OCC windows (requires warmed underlying price)
        final_topics = []
        for sym in symbols:
            eng = MassiveContractEngine(symbol=sym, ws=self.options)
            self.engines[sym] = eng
            self.freshness[sym] = FreshnessTracker()

            occ_codes = await eng.build_occ_list_for_symbol(
                symbol=sym,
                expiry=expiry_map[sym],
                inc_strikes=1
            )

            logger.info("[OCC_INIT] %s → %d contracts", sym, len(occ_codes))
            final_topics.extend(occ_codes)

        # MUST AWAIT — async subscription setter
        await self.options.set_occ_subscriptions(final_topics)

        # Connect Massive WS
        await self.options.connect()

        if self.ib:
            logger.info("[MUX] Underlying feed active via IBKR")

        logger.info("[MUX] Ready — orchestrator may begin evaluation ticks.")

    # ---------------------------------------------------------
    async def close(self):
        try:
            await self.options.close()
        except:
            pass

        if self.ib:
            try:
                await self.ib.close()
            except:
                pass
