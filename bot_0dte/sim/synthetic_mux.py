import asyncio
import time
from typing import Callable, Dict, Any, List


class SyntheticMux:
    """
    Synthetic Mux replicates the interface of the real Mux used by
    IBKR underlying + Massive NBBO adapters.

    It provides:
        - on_underlying(cb)
        - on_option(cb)
        - connect(symbols, expiry_map)

    It DOES NOT generate synthetic data.
    Synthetic feeds call:

        await mux.push_underlying(event)
        await mux.push_option(event)

    which then dispatch to orchestrator callbacks exactly like the real Mux.
    """

    def __init__(self):
        # callbacks registered by orchestrator
        self._underlying_cbs: List[Callable] = []
        self._option_cbs: List[Callable] = []

        # Orchestrator → set by orchestrator.start()
        self.parent_orchestrator = None

        # symbol universe
        self.symbols: List[str] = []
        self.expiry_map: Dict[str, str] = {}

        # connection state
        self._connected = False

    # ----------------------------------------------------------------------
    # VALIDATED CALLBACK REGISTRATION
    # ----------------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        """Register underlying callback, with safety validation."""
        if not callable(cb):
            print(f"[SyntheticMux] ERROR: non-callable passed to on_underlying(): {cb}")
            return
        self._underlying_cbs.append(cb)

    def on_option(self, cb: Callable):
        """Register option callback, with safety validation."""
        if not callable(cb):
            print(f"[SyntheticMux] ERROR: non-callable passed to on_option(): {cb}")
            return
        self._option_cbs.append(cb)

    # ----------------------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        """
        Simulates real Mux.connect():

        - Records symbol universe + expiries
        - Marks the Mux as active
        - No real sockets are opened
        """
        self.symbols = symbols
        self.expiry_map = expiry_map
        self._connected = True

        print(f"[SyntheticMux] Connected to synthetic universe: {symbols}")

    # ----------------------------------------------------------------------
    # EVENT DISPATCHERS
    # ----------------------------------------------------------------------
    async def push_underlying(self, event: Dict[str, Any]):
        """
        Feeds call this to push underlying ticks.
        Mux dispatches to orchestrator like real IBKR adapter.
        """
        if not self._connected:
            return

        for cb in list(self._underlying_cbs):
            try:
                # Real MassiveMux does NOT await in-line — it schedules tasks
                result = cb(event)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)

            except Exception as e:
                print(f"[SyntheticMux] underlying callback error: {e}, event={event}")

    async def push_option(self, event: Dict[str, Any]):
        """
        Feeds call this to push option NBBO updates.
        """
        if not self._connected:
            return

        for cb in list(self._option_cbs):
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)

            except Exception as e:
                print(f"[SyntheticMux] option callback error: {e}, event={event}")
