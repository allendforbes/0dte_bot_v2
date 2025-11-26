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

    Flow:
        IBKR Underlying → ContractEngine (OCC subscription) + Orchestrator
        Massive Options → Orchestrator
    """

    def __init__(
        self, ib_underlying: IBUnderlyingAdapter, options_ws: MassiveOptionsWSAdapter
    ):
        self.ib_underlying = ib_underlying
        self.options = options_ws

        # Callbacks provided by orchestrator
        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        # Created during connect()
        self.contract_engine: ContractEngine = None
        self.parent_orchestrator = None

        # Use IBKR loop for unified scheduling
        self.loop = ib_underlying.loop

    # ------------------------------------------------------------------
    # PUBLIC REGISTRATION
    # ------------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        """Register callback for underlying ticks."""
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        """Register callback for option NBBO ticks."""
        self._option_handlers.append(cb)

    # ------------------------------------------------------------------
    # CONNECTION
    # ------------------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        """
        Connect IBKR (underlying), connect Massive (options),
        and create ContractEngine.
        """

        logger.info(f"[MUX] Connecting hybrid system with {len(symbols)} symbols...")

        # --------------------------------------------------------------
        # 1. Connect IBKR Underlying
        # --------------------------------------------------------------
        logger.info("[MUX] Connecting IBKR underlying...")
        await self.ib_underlying.connect()
        await self.ib_underlying.subscribe(symbols)
        logger.info("[MUX] IBKR underlying connected ✅")

        # --------------------------------------------------------------
        # 2. Connect Massive Options WS
        # --------------------------------------------------------------
        logger.info("[MUX] Connecting Massive options...")
        await self.options.connect()
        logger.info("[MUX] Massive options connected ✅")

        # --------------------------------------------------------------
        # 3. Create ContractEngine
        # --------------------------------------------------------------
        if self.contract_engine is None:
            for s in symbols:
                if s not in expiry_map or not expiry_map[s]:
                    logger.error(f"[MUX] Missing expiry for {s} → cannot trade this symbol")

            self.contract_engine = ContractEngine(
                options_ws=self.options,
                expiry_map=expiry_map
            )

        # --------------------------------------------------------------
        # 4. Wire callbacks
        # --------------------------------------------------------------
        self.ib_underlying.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)

        logger.info("[MUX] All connections established ✅")

    # ------------------------------------------------------------------
    # ROUTING: UNDERLYING → ContractEngine + Orchestrator
    # ------------------------------------------------------------------
    async def _handle_underlying(self, event: Dict[str, Any]):
        """
        Quiet routing:
            - ContractEngine receives underlying tick
            - Orchestrator receives underlying tick
            - No terminal spam
        """
        sym = event.get("symbol")
        if not sym:
            return

        # 1. ContractEngine subscription management
        if self.contract_engine:
            before = list(self.contract_engine.current_subs.get(sym, []))
            await self.contract_engine.on_underlying(event)
            after = list(self.contract_engine.current_subs.get(sym, []))

            # Notify orchestrator of chain refresh
            if before != after and self.parent_orchestrator:
                self.parent_orchestrator.notify_chain_refresh(sym)

        # 2. Forward to orchestrator (quiet)
        for cb in self._underlying_handlers:
            self.loop.create_task(cb(event))

    # ------------------------------------------------------------------
    # ROUTING: OPTIONS → Orchestrator
    # ------------------------------------------------------------------
    async def _handle_option(self, event: Dict[str, Any]):
        """Forward NBBO event to orchestrator (quiet)."""
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
