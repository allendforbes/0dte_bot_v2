"""
bot_start.py — Hybrid Bot Launcher (IBKR + Massive)

Architecture:
    • IBKR → Underlying quotes (SPY, QQQ, etc.)
    • Massive.com → Options NBBO
    • MassiveMux → Unified routing
    • ContractEngine → Auto OCC subscriptions
    • Orchestrator → VWAP + Strategy + Execution
    • ExecutionEngine → Paper mode (IBKR)

Data Flow:
    IBKR TWS/Gateway → IBUnderlyingAdapter → MassiveMux → Orchestrator
    Massive WebSocket → MassiveOptionsWSAdapter → MassiveMux → Orchestrator
"""

import asyncio
import logging

from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


logging.basicConfig(level=logging.INFO)


async def main():
    """
    Main entry point for hybrid IBKR + Massive bot.
    """

    logger = StructuredLogger()
    telemetry = Telemetry()

    # --------------------------------------------------------------
    # 1. IBKR UNDERLYING ADAPTER
    # --------------------------------------------------------------
    print("[BOOT] Initializing IBKR underlying adapter...")
    ib_underlying = IBUnderlyingAdapter(
        host="127.0.0.1", port=4002, client_id=11  # Paper trading port (7497 for live)
    )

    # --------------------------------------------------------------
    # 2. MASSIVE OPTIONS ADAPTER
    # --------------------------------------------------------------
    print("[BOOT] Initializing Massive options adapter...")
    options_ws = MassiveOptionsWSAdapter.from_env()

    # --------------------------------------------------------------
    # 3. HYBRID MUX (IBKR + Massive)
    # --------------------------------------------------------------
    print("[BOOT] Creating hybrid MassiveMux...")
    mux = MassiveMux(ib_underlying=ib_underlying, options_ws=options_ws)

    # --------------------------------------------------------------
    # 4. EXECUTION ENGINE (Paper Mode)
    # --------------------------------------------------------------
    print("[BOOT] Initializing execution engine (PAPER mode)...")
    engine = ExecutionEngine(use_mock=False)  # False = connects to IBKR
    await engine.start()

    # --------------------------------------------------------------
    # 5. ORCHESTRATOR (WS-Native)
    # --------------------------------------------------------------
    print("[BOOT] Creating orchestrator...")
    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        auto_trade_enabled=True,  # Enable trading
        trade_mode="paper",  # Paper mode
    )

    # --------------------------------------------------------------
    # 6. START ORCHESTRATOR (Connects all adapters)
    # --------------------------------------------------------------
    print("\n🚀 Starting hybrid bot (IBKR + Massive)...\n")
    await orch.start()

    # --------------------------------------------------------------
    # 7. KEEP ALIVE LOOP
    # --------------------------------------------------------------
    print("✅ Bot running in PAPER mode.")
    print("   Underlying: IBKR TWS/Gateway")
    print("   Options: Massive.com WebSocket")
    print("\nPress Ctrl+C to stop.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        await mux.close()
        print("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())

async def build_orchestrator_for_sim(symbols):
    """
    Build orchestrator in SIMULATION MODE:
    - No IBKR
    - No Massive WebSocket
    - No account updates
    - No execution engine calls
    """
    from bot_0dte.sim.fake_engine import FakeExecutionEngine
    from bot_0dte.strategy.latency_precheck import LatencyPrecheck
    from bot_0dte.data.providers.massive.massive_mux import MassiveMux
    from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter

    # Fake data pipes
    fake_ib = FakeExecutionEngine.make_fake_underlying_pipe()
    fake_options = MassiveOptionsWSAdapter(api_key="SIM")

    mux = MassiveMux(fake_ib, fake_options)

    # Fake execution engine (no orders)
    engine = FakeExecutionEngine()

    logger = StructuredLogger()
    telemetry = Telemetry()

    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        universe=symbols,
        auto_trade_enabled=True,
        trade_mode="shadow",
    )
    return orch
