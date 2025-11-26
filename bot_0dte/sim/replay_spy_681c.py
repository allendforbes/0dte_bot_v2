"""
Deterministic replay: SPY 681C impulse.
- Feeds Orchestrator with synthetic underlying + NBBO ticks.
- No live connections. Shadow mode only.
- Uses your real EliteEntry/Trail/StrikeSelector/UI paths.
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
# Minimal fake execution engine
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
        self.last_price = {} 

    async def start(self):
        return

    async def send_market(self, **kwargs):
        # Orchestrator will be in shadow mode; this won’t be called.
        return {"status": "shadow", "echo": kwargs}


# -------------------------------------------------------------------
# Minimal dummy mux (no I/O). We never call connect().
# -------------------------------------------------------------------
class DummyMux:
    def __init__(self):
        self._under_handlers: List[Callable] = []
        self._opt_handlers: List[Callable] = []
        # mimic loop.create_task capability
        self.loop = asyncio.get_event_loop()
        self.parent_orchestrator = None

    def on_underlying(self, cb: Callable):
        self._under_handlers.append(cb)

    def on_option(self, cb: Callable):
        self._opt_handlers.append(cb)

    # helpers we’ll call directly in replay
    async def emit_underlying(self, event: Dict[str, Any]):
        for cb in self._under_handlers:
            await cb(event)

    async def emit_option(self, event: Dict[str, Any]):
        for cb in self._opt_handlers:
            await cb(event)

    async def close(self):
        return


# -------------------------------------------------------------------
# Build orchestrator in SIM (no connects)
# -------------------------------------------------------------------
async def build_orchestrator_for_sim(symbols: List[str]) -> Orchestrator:
    logger = StructuredLogger(prefix="SIM", component="REPLAY", level_filter=["EVENT","INFO","WARN","ERROR"])
    telemetry = Telemetry()
    engine = FakeExecutionEngine()
    mux = DummyMux()

    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        universe=symbols,          # ['SPY']
        auto_trade_enabled=True,   # allowed, but trade_mode='shadow'
        trade_mode="shadow",
    )

    # Wire callbacks (same as start() would do), but do NOT call mux.connect()
    mux.on_underlying(orch._on_underlying)
    mux.on_option(orch._on_option)
    mux.parent_orchestrator = orch

    return orch


# -------------------------------------------------------------------
# Generate a few option NBBO ticks for a single OCC (SPY 681C)
# -------------------------------------------------------------------
def _occ(symbol: str, expiry_yyyy_mm_dd: str, strike: float, right: str) -> str:
    return ContractEngine.encode_occ(symbol, expiry_yyyy_mm_dd, right, strike)


# -------------------------------------------------------------------
# Main replay
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

    occ_681C = _occ(symbol, expiry, 681.0, "C")

    # Pre-warm chain freshness (what MassiveMux would do on subscription change)
    orch.notify_chain_refresh(symbol)
    # Seed NBBO for 681C so ChainAggregator isn’t empty/stale
    nbbo_series = [
        (1.40, 1.44),
        (1.44, 1.48),
        (1.47, 1.51),
        (1.50, 1.54),
    ]
    for bid, ask in nbbo_series:
        await mux.emit_option({
            "symbol": symbol,
            "contract": occ_681C,
            "strike": 681.0,
            "right": "C",
            "bid": bid,
            "ask": ask,
            "_recv_ts": time.time(),
        })
        await asyncio.sleep(0.05)


    # Seed NBBO for 681C so ChainAggregator isn’t empty/stale
    nbbo_series = [
        (1.40, 1.44),
        (1.44, 1.48),
        (1.47, 1.51),
        (1.50, 1.54),
    ]
    for bid, ask in nbbo_series:
        await mux.emit_option({
            "symbol": symbol,
            "contract": occ_681C,
            "strike": 681.0,
            "right": "C",
            "bid": bid,
            "ask": ask,
            "_recv_ts": time.time(),
        })
        await asyncio.sleep(0.05)

    # Underlying replay — a clean impulse that should trigger CALL signal,
    # select 681C, shadow enter, trail, then exit on small pullback.
    underlying_series = [
        680.80, 680.95, 681.10, 681.30, 681.55, 681.80,  # build
        682.10, 682.40,                                  # breakout (signal)
        682.70, 682.95,                                  # continuation
        682.50, 682.10,                                  # pullback → likely trail exit
    ]

    for px in underlying_series:
    # NBBO tracking so trail + PnL update correctly
        mid = 1.55 + (px - 681.0) * 0.10
        await mux.emit_option({
            "symbol": symbol,
            "contract": occ_681C,
            "strike": 681.0,
            "right": "C",
            "bid": max(0.05, mid - 0.02),
            "ask": mid + 0.02,
            "_recv_ts": time.time(),
        })
        await asyncio.sleep(0.10)

        # make StrikeSelector happy
        orch.engine.last_price[symbol] = px

        await mux.emit_underlying({
            "symbol": symbol,
            "price": px,
            "bid": px - 0.01,
            "ask": px + 0.01,
            "_recv_ts": time.time(),
        })

        # keep NBBO moving roughly with underlying
        mid = 1.55 + (px - 681.0) * 0.10
        await mux.emit_option({
            "symbol": symbol,
            "contract": occ_681C,
            "strike": 681.0,
            "right": "C",
            "bid": max(0.05, mid - 0.02),
            "ask": mid + 0.02,
            "_recv_ts": time.time(),
        })

        await asyncio.sleep(0.10)

    print("\n[SIM] Replay complete.\n")


if __name__ == "__main__":
    asyncio.run(main())

