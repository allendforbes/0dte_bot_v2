"""
MassiveMux — Unified WebSocket Router

Responsibilities:
    • Connect both stocks and options WebSockets
    • Subscribe to underlyings via stocks WS
    • Create ContractEngine for OCC subscription management
    • Route events to orchestrator callbacks
    • Handle reconnects
"""

import asyncio
import logging
from typing import Callable, List, Dict, Any

from .massive_stocks_ws_adapter import MassiveStocksWSAdapter
from .massive_options_ws_adapter import MassiveOptionsWSAdapter
from .massive_contract_engine import ContractEngine


logger = logging.getLogger(__name__)


class MassiveMux:
    """
    Central hub that unifies:
        • MassiveStocksWSAdapter  (underlyings: Q.*)
        • MassiveOptionsWSAdapter (options NBBO: NO / OQ)
        • ContractEngine          (auto OCC subscription logic)

    Flow:
        Stocks WS → ContractEngine (OCC subscription) + Orchestrator
        Options WS → Orchestrator
    """

    def __init__(
        self, stocks_ws: MassiveStocksWSAdapter, options_ws: MassiveOptionsWSAdapter
    ):
        self.stocks = stocks_ws
        self.options = options_ws
        self.loop = stocks_ws.loop

        # Callbacks wired from Orchestrator
        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        self.contract_engine = None

    # ------------------------------------------------------------------
    #   PUBLIC REGISTRATION
    # ------------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        """Register callback for underlying ticks."""
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        """Register callback for option NBBO ticks."""
        self._option_handlers.append(cb)

    # ------------------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        """
        Connect stocks + options + contract engine.

        Args:
            symbols: List of underlying symbols (e.g., ["SPY", "QQQ"])
            expiry_map: Map of symbol -> expiry date (e.g., {"SPY": "2024-11-22"})
        """
        logger.info(f"[MUX] Connecting to {len(symbols)} underlyings...")

        # --------------------------------------------------------------
        # 1. Connect STOCKS feed
        # --------------------------------------------------------------
        await self.stocks.connect()
        await self.stocks.subscribe(symbols)
        logger.info(f"[MUX] Stocks WS connected and subscribed")

        # --------------------------------------------------------------
        # 2. Connect OPTIONS feed
        # --------------------------------------------------------------
        await self.options.connect()
        logger.info(f"[MUX] Options WS connected")

        # --------------------------------------------------------------
        # 3. Create ContractEngine with expiry_map
        # --------------------------------------------------------------
        self.contract_engine = ContractEngine(
            options_ws=self.options, expiry_map=expiry_map
        )
        logger.info(f"[MUX] ContractEngine initialized")

        # --------------------------------------------------------------
        # 4. Wire callbacks
        # --------------------------------------------------------------
        self.stocks.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)

        logger.info(f"[MUX] All connections established ✅")

    # ------------------------------------------------------------------
    #   ROUTING
    # ------------------------------------------------------------------
    async def _handle_underlying(self, event: Dict[str, Any]):
        """
        Route underlying tick to:
            1. ContractEngine (OCC subscription management)
            2. Orchestrator callbacks

        Event is already normalized by stocks adapter:
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
        Route option NBBO tick to Orchestrator.

        Event is already normalized by options adapter:
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
    async def close(self):
        """Gracefully close both WebSocket connections."""
        logger.info("[MUX] Closing connections...")
        await self.stocks.close()
        await self.options.close()
        logger.info("[MUX] Connections closed")
