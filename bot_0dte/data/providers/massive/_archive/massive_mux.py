# massive_mux.py — Unified Massive WebSocket Mux
# Connects Stocks + Options adapters and forwards events to orchestrator

import asyncio
from typing import Callable, List, Dict, Any


class MassiveMux:
    """
    Unified router for Massive Stocks + Options WS adapters.

    Responsibilities:
        - Start both WS adapters
        - Subscribe to symbols/contracts
        - Forward ticks to orchestrator callbacks
        - Provide stable async API
    """

    def __init__(self, stocks_adapter, options_adapter):
        self.stocks = stocks_adapter
        self.options = options_adapter

        # callbacks registered by orchestrator
        self._underlying_cbs: List[Callable] = []
        self._option_cbs: List[Callable] = []

    # ------------------------------------------------------------
    # Orchestrator registration
    # ------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        self._underlying_cbs.append(cb)

    def on_option(self, cb: Callable):
        self._option_cbs.append(cb)

    # ------------------------------------------------------------
    async def connect(self, symbols: List[str]):
        """
        Connect both WS feeds and subscribe to symbols + inferred contracts.
        For now, contract selection is left to orchestrator.
        """

        # register internal routing
        self.stocks.on_tick(self._route_underlying)
        self.options.on_tick(self._route_option)

        # connect
        await self.stocks.connect()
        await self.options.connect()

        # subscribe underlyings
        await self.stocks.subscribe(symbols)

    # ------------------------------------------------------------
    # Tick routing
    # ------------------------------------------------------------
    async def _route_underlying(self, event: Dict[str, Any]):
        for cb in self._underlying_cbs:
            asyncio.create_task(cb(event))

    async def _route_option(self, event: Dict[str, Any]):
        for cb in self._option_cbs:
            asyncio.create_task(cb(event))

    # ------------------------------------------------------------
    async def subscribe_contracts(self, contracts: List[str]):
        """
        Called by orchestrator after ATM±2 selection.
        """
        await self.options.subscribe(contracts)

    # ------------------------------------------------------------
    async def close(self):
        await self.stocks.close()
        await self.options.close()
