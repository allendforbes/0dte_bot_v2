"""
MassiveMux — Hybrid Router (IBKR Underlying + Massive Options)
"""

import asyncio
import logging
from typing import Callable, List, Dict, Any

from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from .massive_options_ws_adapter import MassiveOptionsWSAdapter
from .massive_contract_engine import ContractEngine

logger = logging.getLogger(__name__)


class MassiveMux:
    """
    Hybrid data router.

    Unifies:
        • IBUnderlyingAdapter (real-time underlying quotes)
        • MassiveOptionsWSAdapter (options NBBO)
        • ContractEngine (auto OCC subscription logic)
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
        self.contract_engine: ContractEngine = None
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

        # 3. CONTRACT ENGINE
        if self.contract_engine is None:
            self.contract_engine = ContractEngine(
                options_ws=self.options,
                expiry_map=expiry_map
            )

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
        if not self.contract_engine:
            return

        logger.info("[MUX] Massive reconnected — resubscribing OCC contracts...")
        await self.contract_engine.resubscribe_all()

    # ------------------------------------------------------------------
    # ROUTING: UNDERLYING
    # ------------------------------------------------------------------
    async def _handle_underlying(self, event: Dict[str, Any]):
        sym = event.get("symbol")
        if not sym:
            return

        # contract engine updates ATM cluster
        if self.contract_engine:
            before = list(self.contract_engine.current_subs.get(sym, []))
            await self.contract_engine.on_underlying(event)
            after = list(self.contract_engine.current_subs.get(sym, []))

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
