"""
MassiveMux v3.0 — Hybrid Router (IBKR Underlying + Massive NBBO/OQ)
-------------------------------------------------------------------
• No initial OCC subscription — waits for first IBKR tick
• Pure OCC → adapter auto-prefixes to Q.O:<occ>
• Correct reconnect semantics
• No concurrency hazards
"""

import logging
from typing import Callable, List, Dict, Any

from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from .massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine

logger = logging.getLogger(__name__)


class MassiveMux:
    def __init__(self, ib_underlying: IBUnderlyingAdapter, options_ws: MassiveOptionsWSAdapter):
        self.ib_underlying = ib_underlying
        self.options = options_ws

        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        self.contract_engines: Dict[str, MassiveContractEngine] = {}

        self.parent_orchestrator = None
        self.loop = ib_underlying.loop

    # ------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        self._option_handlers.append(cb)

    # ------------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        logger.info(f"[MUX] Connecting with universe: {symbols}")

        # 1 — Underlying
        await self.ib_underlying.connect()
        await self.ib_underlying.subscribe(symbols)

        # 2 — Massive
        await self.options.connect()

        # 3 — Create engines (no initial subs)
        for sym in symbols:
            self.contract_engines[sym] = MassiveContractEngine(sym, self.options)

        # 4 — Reconnect handler
        self.options.on_reconnect(self._on_massive_reconnect)

        # 5 — Wire streams
        self.ib_underlying.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)

        logger.info("[MUX] All connections established")

    # ------------------------------------------------------------
    async def _on_massive_reconnect(self):
        logger.warning("[MUX] Massive reconnected → resubscribe OCC")
        for eng in self.contract_engines.values():
            await eng.resubscribe_all()

    # ------------------------------------------------------------
    async def _handle_underlying(self, event: Dict[str, Any]):
        sym = event.get("symbol")
        if not sym:
            return

        eng = self.contract_engines.get(sym)
        if eng:
            before = list(eng.contracts)
            await eng.on_underlying(event)
            after = list(eng.contracts)

            if before != after and self.parent_orchestrator:
                self.parent_orchestrator.notify_chain_refresh(sym)

        for cb in self._underlying_handlers:
            self.loop.create_task(cb(event))

    # ------------------------------------------------------------
    async def _handle_option(self, event: Dict[str, Any]):
        for cb in self._option_handlers:
            self.loop.create_task(cb(event))

    # ------------------------------------------------------------
    async def close(self):
        await self.ib_underlying.close()
        await self.options.close()
