import asyncio
import argparse
import time
import random

from bot_0dte.sim.synthetic_mux import SyntheticMux
from bot_0dte.sim.synthetic_underlying_feed import SyntheticUnderlyingFeed
from bot_0dte.sim.synthetic_nbbo_feed import SyntheticNBBOFeed

from bot_0dte.orchestrator import Orchestrator
from bot_0dte.execution.adapters.mock_exec import MockExecutionEngine
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.universe import get_expiry_for_symbol


SCENARIOS = {
    "trend_up": dict(drift=+0.10, volatility=0.4),
    "trend_down": dict(drift=-0.10, volatility=0.4),
    "chop": dict(drift=0.0, volatility=1.5),
    "gamma_squeeze": dict(drift=+0.20, volatility=1.2),
    "iv_collapse": dict(drift=0.02, volatility=0.2),
    "premium_crush": dict(drift=0.05, volatility=0.1),
}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", type=str, default="SPY")
    ap.add_argument("--scenario", type=str, default="trend_up",
                    choices=list(SCENARIOS.keys()))
    ap.add_argument("--duration", type=float, default=15.0)
    return ap.parse_args()


async def main():
    args = parse_args()

    symbol = args.symbol.upper()
    scenario = SCENARIOS[args.scenario]

    # ------------------------------------------------------------------
    # Synthetic Mux
    # ------------------------------------------------------------------
    mux = SyntheticMux()

    # ------------------------------------------------------------------
    # Exec Engine + Logging
    # ------------------------------------------------------------------
    mock_engine = MockExecutionEngine()
    telemetry = Telemetry()
    logger = StructuredLogger("synthetic_replay")

    # ------------------------------------------------------------------
    # Orchestrator (A2-M logic)
    # ------------------------------------------------------------------
    orch = Orchestrator(
        engine=mock_engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        universe=[symbol],
        auto_trade_enabled=False,
        trade_mode="shadow",
    )

    # Bind orchestrator → mux (chain refresh callbacks)
    mux.parent_orchestrator = orch

    # ------------------------------------------------------------------
    # Mux “connect” — chain engines + freshness objects initialized
    # ------------------------------------------------------------------
    expiry = get_expiry_for_symbol(symbol)
    await mux.connect([symbol], {symbol: expiry})

    # Ensure orchestrator sees SyntheticMux freshness pool
    orch.freshness = mux.freshness

    # ------------------------------------------------------------------
    # Synthetic underlying feed
    # ------------------------------------------------------------------
    start_price = 400 if symbol == "SPY" else 100
    under = SyntheticUnderlyingFeed(
        mux=mux,
        symbol=symbol,
        start_price=start_price,
        drift=scenario["drift"],
        volatility=scenario["volatility"],
    )

    # ------------------------------------------------------------------
    # Synthetic NBBO feed (with realistic greeks + microstructure)
    # ------------------------------------------------------------------
    strike_inc = 1 if symbol != "NVDA" else 5
    nbbo = SyntheticNBBOFeed(
        mux=mux,
        symbol=symbol,
        expiry=expiry,
        underlying=under,
        strike_inc=strike_inc,
    )

    # ------------------------------------------------------------------
    # Start orchestrator (NON-BLOCKING)
    # ------------------------------------------------------------------
    asyncio.create_task(orch.start())   # <-- Correct behavior

    # ------------------------------------------------------------------
    # Start synthetic data feeds
    # ------------------------------------------------------------------
    print(f"\n[SIM] Starting A2-M synthetic simulation for {symbol}")
    print(f"[SIM] Scenario: {args.scenario} (drift={scenario['drift']}, σ={scenario['volatility']})")
    print("[SIM] Running...\n")

    task_under = asyncio.create_task(under.start())
    task_nbbo = asyncio.create_task(nbbo.start())

    # Allow simulation to execute
    await asyncio.sleep(args.duration)

    # ------------------------------------------------------------------
    # Shutdown feeds
    # ------------------------------------------------------------------
    under.stop()
    nbbo.stop()

    await asyncio.sleep(0.25)

    print("\n[SIM] Complete.")
    print("[SIM] Review logs for:")
    print("      • signal generation")
    print("      • strike selection")
    print("      • latency gating")
    print("      • premium / convexity filters\n")

    task_under.cancel()
    task_nbbo.cancel()
    await asyncio.gather(task_under, task_nbbo, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
