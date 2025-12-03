"""
MassiveMux — Hybrid Router (IBKR Underlying + Massive Options)
"""

import asyncio
import logging
from typing import Callable, List, Dict, Any

from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from .massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine

logger = logging.getLogger(__name__)


class MassiveMux:
    """
    Hybrid data router.

    Unifies:
        • IBUnderlyingAdapter (real-time underlying quotes)
        • MassiveOptionsWSAdapter (options NBBO)
        • MassiveContractEngine (dynamic OCC subscription)
    """

    def __init__(
        self, ib_underlying: IBUnderlyingAdapter, options_ws: MassiveOptionsWSAdapter
    ):
        self.ib_underlying = ib_underlying
        self.options = options_ws

        # callbacks provided by orchestrator
        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        # created during connect()
        self.contract_engine: MassiveContractEngine = None
        self.parent_orchestrator = None

        # use IBKR's event loop
        self.loop = ib_underlying.loop

    # ------------------------------------------------------------------
    # PUBLIC REGISTRATION
    # ------------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        self._option_handlers.append(cb)

    # ------------------------------------------------------------------
    # CONNECTION
    # ------------------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        logger.info(f"[MUX] Connecting hybrid system with {len(symbols)} symbols...")

        # 1. IBKR UNDERLYING
        logger.info("[MUX] Connecting IBKR underlying...")
        await self.ib_underlying.connect()
        await self.ib_underlying.subscribe(symbols)
        logger.info("[MUX] IBKR underlying connected ✅")

        # 2. MASSIVE OPTIONS
        logger.info("[MUX] Connecting Massive options...")
        await self.options.connect()
        logger.info("[MUX] Massive options connected ✅")

        # 3. CONTRACT ENGINE (ONE PER SYMBOL)
        if self.contract_engine is None:
            self.contract_engine = {}
            for sym in symbols:
                self.contract_engine[sym] = MassiveContractEngine(
                    symbol=sym,
                    ws=self.options,
                )
                # initial subscription
                await self.contract_engine[sym].refresh_contracts()

        # 4. WIRE RECONNECT
        self.options.on_reconnect(self._on_massive_reconnect)

        # 5. ROUTING
        self.ib_underlying.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)

        logger.info("[MUX] All connections established ✅")

    # ------------------------------------------------------------------
    # RECONNECT HANDLER
    # ------------------------------------------------------------------
    async def _on_massive_reconnect(self):
        logger.info("[MUX] Massive reconnected — resubscribing OCC contracts...")
        for eng in self.contract_engine.values():
            await eng.resubscribe_all()

    # ------------------------------------------------------------------
    # ROUTING: UNDERLYING
    # ------------------------------------------------------------------
    async def _handle_underlying(self, event: Dict[str, Any]):
        sym = event.get("symbol")
        if not sym:
            return

        eng = self.contract_engine.get(sym)
        if eng:
            before = list(eng.contracts)
            await eng.on_underlying(event)
            after = list(eng.contracts)

            if before != after and self.parent_orchestrator:
                self.parent_orchestrator.notify_chain_refresh(sym)

        # forward to orchestrator
        for cb in self._underlying_handlers:
            self.loop.create_task(cb(event))

    # ------------------------------------------------------------------
    # ROUTING: OPTION
    # ------------------------------------------------------------------
    async def _handle_option(self, event: Dict[str, Any]):
        for cb in self._option_handlers:
            self.loop.create_task(cb(event))

    # ------------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------------
    async def close(self):
        logger.info("[MUX] Closing connections...")
        await self.ib_underlying.close()
        await self.options.close()
        logger.info("[MUX] Connections closed")
