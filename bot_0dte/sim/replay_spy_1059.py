"""
Deterministic replay: SPY 10:59 breakout (focus on early micro-reclaim).
- Feeds Orchestrator with synthetic underlying + NBBO ticks.
- No live connections. Shadow mode only.
- Uses your real EliteEntry/Trail/Selector/UI (no shortcuts).
"""

import asyncio
import time
from typing import Any, Callable, Dict, List

# Orchestrator & infra
from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.universe import get_expiry_for_symbol
from bot_0dte.data.providers.massive.massive_contract_engine import ContractEngine


# -------------------------------------------------------------------
# Minimal fake execution engine (paper-shadow)
# -------------------------------------------------------------------
class _AcctState:
    def __init__(self, net_liq: float):
        self.net_liq = net_liq
        self._fresh = True

    def is_fresh(self) -> bool:
        return self._fresh


class FakeExecutionEngine:
    """Satisfies Orchestrator’s engine interface in shadow mode."""

    def __init__(self, net_liq: float = 25_000):
        self.account_state = _AcctState(net_liq)
        self.last_price: Dict[str, float] = {}

    async def start(self):
        return

    async def send_market(self, **kwargs):
        # Orchestrator is in shadow mode for this replay.
        return {"status": "shadow", "echo": kwargs}


# -------------------------------------------------------------------
# Minimal dummy mux (no I/O). We never call connect().
# -------------------------------------------------------------------
class DummyMux:
    def __init__(self):
        self._under_handlers: List[Callable] = []
        self._opt_handlers: List[Callable] = []
        self.loop = asyncio.get_event_loop()
        self.parent_orchestrator = None

    def on_underlying(self, cb: Callable):
        self._under_handlers.append(cb)

    def on_option(self, cb: Callable):
        self._opt_handlers.append(cb)

    async def emit_underlying(self, event: Dict[str, Any]):
        for cb in self._under_handlers:
            await cb(event)

    async def emit_option(self, event: Dict[str, Any]):
        for cb in self._opt_handlers:
            await cb(event)

    async def close(self):
        return


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _occ(symbol: str, expiry_yyyy_mm_dd: str, strike: float, right: str) -> str:
    return ContractEngine.encode_occ(symbol, expiry_yyyy_mm_dd, right, strike)


async def build_orchestrator_for_sim(symbols: List[str]) -> Orchestrator:
    # Only EVENT/INFO/WARN/ERROR to keep it readable
    logger = StructuredLogger(prefix="SIM", component="REPLAY_1059",
                              level_filter=["EVENT", "INFO", "WARN", "ERROR"])
    telemetry = Telemetry()
    engine = FakeExecutionEngine()
    mux = DummyMux()

    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        universe=symbols,          # ['SPY']
        auto_trade_enabled=True,   # allowed; still 'shadow' mode
        trade_mode="shadow",
    )

    # Wire callbacks (same as orch.start would do)
    mux.on_underlying(orch._on_underlying)
    mux.on_option(orch._on_option)
    mux.parent_orchestrator = orch
    return orch


# -------------------------------------------------------------------
# Main replay (dense ticks around 10:58–11:01)
# -------------------------------------------------------------------
async def main():
    symbol = "SPY"
    orch = await build_orchestrator_for_sim([symbol])
    mux: DummyMux = orch.mux  # type: ignore

    # OCC + chain prep
    expiry = get_expiry_for_symbol(symbol)
    if not expiry:
        print("No expiry for SPY today — universe logic returned None.")
        return

    # We target 681C for this replay
    occ_681C = _occ(symbol, expiry, 681.0, "C")

    # Tell orchestrator the chain just refreshed (as MassiveMux would)
    orch.notify_chain_refresh(symbol)

    # Seed NBBO for 681C so chain isn’t empty/stale
    # (start ~1.00 mid and lift with the impulse)
    nbbo_seed = [
        (0.96, 1.00),
        (0.98, 1.02),
        (1.00, 1.04),
    ]
    for bid, ask in nbbo_seed:
        await mux.emit_option({
            "symbol": symbol,
            "contract": occ_681C,
            "strike": 681.0,
            "right": "C",
            "bid": bid,
            "ask": ask,
            "_recv_ts": time.time(),
        })
        await asyncio.sleep(0.03)

    # Underlying around 10:55–11:05 (compressed timeline).
    # We create a shallow dip -> micro reclaim -> fast ramp.
    underlying_series = [
        # pre-break drift
        680.40, 680.35, 680.30, 680.28, 680.26,
        680.25, 680.27, 680.29,
        # micro reclaim starts ~10:58:40
        680.35, 680.42, 680.50,
        680.58, 680.66,
        # push into 10:59 window (decisive)
        680.74, 680.83, 680.92,
        681.01, 681.10,  # <- early qualify should trigger here if dev_change flips positive hard
        # continuation after entry
        681.22, 681.35, 681.48, 681.60,
        681.72, 681.85,
        # controlled pullback (for trail test)
        681.58, 681.42, 681.30,
    ]

    # Play the tape: for each underlying tick, keep 681C NBBO moving with it
    base_mid = 1.00
    for px in underlying_series:
        # let StrikeSelector see "underlying last"
        orch.engine.last_price[symbol] = px

        await mux.emit_underlying({
            "symbol": symbol,
            "price": px,
            "bid": px - 0.02,
            "ask": px + 0.02,
            "_recv_ts": time.time(),
        })

        # rudimentary call price response: +0.07 per +0.10 underlying
        mid = base_mid + (px - 680.50) * 0.70
        await mux.emit_option({
            "symbol": symbol,
            "contract": occ_681C,
            "strike": 681.0,
            "right": "C",
            "bid": max(0.05, mid - 0.02),
            "ask": mid + 0.02,
            "_recv_ts": time.time(),
        })

        await asyncio.sleep(0.07)

    print("\n[SIM] 10:59 replay complete.\n")
    print("Look for:")
    print("  • EVENT signal_generated / [SIGNAL] line near 681.00 print")
    print("  • EVENT shadow_entry with contract=681C")
    print("  • trail updates + possible shadow_exit on pullback")


if __name__ == "__main__":
    asyncio.run(main())

