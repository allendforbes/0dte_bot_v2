"""
MassiveMux — Hybrid Router (IBKR Underlying + Massive Options)

Responsibilities:
    • Route underlying ticks from IBKR adapter
    • Route options NBBO from Massive adapter
    • Create ContractEngine for OCC subscription management
    • Handle reconnects for both data sources

Architecture:
    IBKR Adapter (underlying) → MassiveMux → Orchestrator
    Massive Options (NBBO)    → MassiveMux → Orchestrator
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
        """
        Initialize hybrid mux.

        Args:
            ib_underlying: IBKR underlying adapter
            options_ws: Massive options WebSocket adapter
        """
        self.ib_underlying = ib_underlying
        self.options = options_ws
        self.loop = ib_underlying.loop

        # Callbacks wired from Orchestrator
        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        self.contract_engine = None

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
        Connect both data sources + create contract engine.

        Args:
            symbols: List of underlying symbols (e.g., ["SPY", "QQQ"])
            expiry_map: Map of symbol -> expiry date (e.g., {"SPY": "2024-11-22"})
        """
        logger.info(f"[MUX] Connecting hybrid system with {len(symbols)} symbols...")

        # --------------------------------------------------------------
        # 1. Connect IBKR UNDERLYING
        # --------------------------------------------------------------
        logger.info("[MUX] Connecting IBKR underlying...")
        await self.ib_underlying.connect()
        await self.ib_underlying.subscribe(symbols)
        logger.info("[MUX] IBKR underlying connected ✅")

        # --------------------------------------------------------------
        # 2. Connect MASSIVE OPTIONS
        # --------------------------------------------------------------
        logger.info("[MUX] Connecting Massive options...")
        await self.options.connect()
        logger.info("[MUX] Massive options connected ✅")

        # --------------------------------------------------------------
        # 3. Create ContractEngine with expiry_map
        # --------------------------------------------------------------
        self.contract_engine = ContractEngine(
            options_ws=self.options, expiry_map=expiry_map
        )
        logger.info("[MUX] ContractEngine initialized")

        # --------------------------------------------------------------
        # 4. Wire callbacks
        # --------------------------------------------------------------
        self.ib_underlying.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)

        logger.info("[MUX] All connections established ✅")

    # ------------------------------------------------------------------
    # ROUTING
    # ------------------------------------------------------------------
    async def _handle_underlying(self, event: Dict[str, Any]):
        """
        Route underlying tick from IBKR to:
            1. ContractEngine (OCC subscription management)
            2. Orchestrator callbacks

        Event format (from IBUnderlyingAdapter):
        {
            "symbol": str,
            "price": float,
            "bid": float | None,
            "ask": float | None,
            "_recv_ts": float
        }
        """
        # 1. Notify ContractEngine (manages OCC subscriptions)
        if self.contract_engine:
            await self.contract_engine.on_underlying(event)

        # 2. Notify Orchestrator
        for cb in self._underlying_handlers:
            self.loop.create_task(cb(event))

    async def _handle_option(self, event: Dict[str, Any]):
        """
        Route option NBBO tick from Massive to Orchestrator.

        Event format (from MassiveOptionsWSAdapter):
        {
            "symbol": str,
            "contract": str,
            "strike": float,
            "right": "C" | "P",
            "bid": float,
            "ask": float,
            "_recv_ts": float
        }
        """
        for cb in self._option_handlers:
            self.loop.create_task(cb(event))

    # ------------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------------
    async def close(self):
        """Gracefully close both data sources."""
        logger.info("[MUX] Closing connections...")
        await self.ib_underlying.close()
        await self.options.close()
        logger.info("[MUX] Connections closed")
