# bot_0dte/data/providers/massive/massive_mux.py

import asyncio
from typing import Callable, List, Dict, Any

from .massive_stocks_ws_adapter import MassiveStocksWSAdapter
from .massive_options_ws_adapter import MassiveOptionsWSAdapter
from .massive_contract_engine import ContractEngine


class MassiveMux:
    """
    MassiveMux
    ----------
    Central hub that unifies:
        • MassiveStocksWSAdapter  (underlyings: Q.*)
        • MassiveOptionsWSAdapter (options NBBO: NO / OQ)
        • ContractEngine          (auto OCC subscription logic)

    Responsibilities:
        • Connect BOTH websockets
        • Auto-subscribe underlyings & options
        • Forward STOCK ticks to orchestrator
        • Forward NBBO ticks to orchestrator
        • Handle reconnects cleanly
    """

    def __init__(self, api_key: str, loop=None):
        self.loop = loop or asyncio.get_event_loop()

        # WS Adapters
        self.stocks = MassiveStocksWSAdapter(api_key, loop=self.loop)
        self.options = MassiveOptionsWSAdapter(api_key, loop=self.loop)

        # Callbacks wired from Orchestrator
        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        self.contract_engine = None

    # ------------------------------------------------------------------
    #   PUBLIC REGISTRATION
    # ------------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        self._option_handlers.append(cb)

    # ------------------------------------------------------------------
    async def connect(self, symbols: List[str]):
        """
        Connect stocks + options + contract engine.
        """
        # Connect ST0CKS feed
        await self.stocks.connect()
        await self.stocks.subscribe(symbols)

        # Connect OPTIONS feed
        await self.options.connect()

        # Build ContractEngine now that WS is live
        self.contract_engine = ContractEngine(
            options_ws=self.options,
            stocks_ws=self.stocks,
            orchestrator=None,  # orchestrator attaches this later
        )

        # Wire callbacks
        self.stocks.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)

    # ------------------------------------------------------------------
    #   ROUTING
    # ------------------------------------------------------------------
    async def _handle_underlying(self, event: Dict[str, Any]):
        """
        STOCKS → notify contract engine + orchestrator
        """
        # Notify contract engine so it can update OCC subscriptions
        if self.contract_engine:
            await self.contract_engine.on_underlying(event)

        # Notify orchestrator
        for cb in self._underlying_handlers:
            self.loop.create_task(cb(event))

    async def _handle_option(self, event: Dict[str, Any]):
        for cb in self._option_handlers:
            self.loop.create_task(cb(event))

    # ------------------------------------------------------------------
    async def close(self):
        await self.stocks.close()
        await self.options.close()

