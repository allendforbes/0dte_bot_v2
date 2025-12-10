import asyncio
from typing import Callable, Dict, Any, List

from bot_0dte.chain.chain_freshness_v2 import ChainFreshnessV2
from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine


class _DummyWS:
    """
    Minimal stand-in for MassiveOptionsWSAdapter / WSAdapterPRO.

    MassiveContractEngine only calls:
        await ws.subscribe_contracts(list_of_pure_occ_codes)

    Safe no-op inside simulation.
    """
    async def subscribe_contracts(self, occ_list):
        return


class SyntheticMux:
    """
    SyntheticMux v3.4 — EXACT MassiveMux semantics for simulation.

    Provides:
        • on_underlying(cb)
        • on_option(cb)
        • connect(symbols, expiry_map)
        • contract_engines[symbol]
        • freshness[symbol] (ChainFreshnessV2)
        • push_option_batch(batch) — PRO-style batch NBBO
        • Emits PURE OCC for aggregator (critical)
    """

    def __init__(self):
        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        self.symbols: List[str] = []
        self.expiry_map: Dict[str, str] = {}

        self.contract_engines: Dict[str, MassiveContractEngine] = {}
        self.freshness: Dict[str, ChainFreshnessV2] = {}

        self.parent_orchestrator = None
        self.loop = asyncio.get_event_loop()

        self._ws = _DummyWS()  # Provided to MassiveContractEngine

    # ------------------------------------------------------------------
    def on_underlying(self, cb: Callable):
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        self._option_handlers.append(cb)

    # ------------------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        """
        Fully mirrors MassiveMux.connect()
        """

        self.symbols = symbols
        self.expiry_map = expiry_map

        for sym in symbols:
            # Freshness tracker
            self.freshness[sym] = ChainFreshnessV2()

            # Contract engine requires ws
            self.contract_engines[sym] = MassiveContractEngine(sym, self._ws)

        print(f"[SyntheticMux] Connected to synthetic universe: {symbols}")

    # ------------------------------------------------------------------
    async def push_underlying(self, event: Dict[str, Any]):
        """
        Exactly like MassiveMux._handle_underlying()
        """

        sym = event.get("symbol")
        if not sym:
            return

        eng = self.contract_engines.get(sym)
        if eng:
            await eng.on_underlying(event)

        # Fanout to handlers
        for cb in list(self._underlying_handlers):
            out = cb(event)
            if asyncio.iscoroutine(out):
                self.loop.create_task(out)

    # ------------------------------------------------------------------
    async def push_option_batch(self, batch: List[Dict[str, Any]]):
        """
        Handles PRO-style batches:
            { "sym": "O:SPY20250117C00400000", "b": 1.23, "a": 1.25, "ts": ... }

        MUST:
            • hydrate ChainFreshnessV2 heartbeat/frame
            • convert OCC from prefixed → PURE
            • expand to aggregator-friendly NBBO row
            • fire per-option handlers like MassiveMux
        """
        if not batch:
            return

        occ_prefixed = batch[0].get("sym")
        if not occ_prefixed:
            return

        # Extract pure OCC + base symbol for freshness
        try:
            pure = occ_prefixed.split(":")[1]

            # Extract underlying (letters until first digit)
            i = 0
            while i < len(pure) and pure[i].isalpha():
                i += 1
            base_symbol = pure[:i]

        except Exception:
            return

        fr = self.freshness.get(base_symbol)
        if fr:
            fr.update_heartbeat()

        # Unroll batch
        for row in batch:

            occ_prefixed = row["sym"]

            try:
                pure = occ_prefixed.split(":")[1]

                # Extract underlying (variable length)
                i = 0
                while i < len(pure) and pure[i].isalpha():
                    i += 1
                symbol = pure[:i]            # SPY, TSLA, NVDA, AAPL, etc.

                expiry = pure[i:i+8]         # YYYYMMDD
                right = pure[i+8]            # C or P
                strike = int(pure[i+9:]) / 1000.0

            except Exception:
                continue

            expanded = {
                "symbol": symbol,
                "expiry": expiry,
                "contract": pure,             # PURE OCC
                "right": right,
                "strike": float(strike),
                "bid": row["b"],
                "ask": row["a"],
                "premium": (row["b"] + row["a"]) / 2,
                "_recv_ts": row["ts"],
            }

            if fr:
                fr.update_frame()

            # Debug NBBO printout so you SEE it flow
            print("EXPANDED NBBO:", expanded)

            # Fanout to orchestrator
            for cb in list(self._option_handlers):
                out = cb(expanded)
                if asyncio.iscoroutine(out):
                    self.loop.create_task(out)

    # ------------------------------------------------------------------
    async def close(self):
        pass
