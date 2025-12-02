"""
bot_start.py — Hybrid Bot Launcher (IBKR + Massive OPTIONS ONLY)

Matches CURRENT MassiveMux signature:
    MassiveMux(ib_underlying, options_ws)
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
    logger = StructuredLogger()
    telemetry = Telemetry()

    print("[BOOT] Initializing IBKR underlying adapter...")
    ib_underlying = IBUnderlyingAdapter(
        host="127.0.0.1",
        port=4002,       # Paper trading
        client_id=11
    )

    print("[BOOT] Initializing Massive OPTIONS adapter...")
    options_ws = MassiveOptionsWSAdapter.from_env()

    print("[BOOT] Creating hybrid MassiveMux...")
    mux = MassiveMux(
        ib_underlying=ib_underlying,
        options_ws=options_ws,    # ✔ match real MassiveMux signature
    )

    print("[BOOT] Initializing execution engine (PAPER mode)...")
    engine = ExecutionEngine(use_mock=False)
    await engine.start()

    print("[BOOT] Creating orchestrator...")
    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        auto_trade_enabled=True,
        trade_mode="paper",
    )

    print("\n🚀 Starting hybrid bot (IBKR + Massive OPTIONS)...\n")
    await orch.start()

    print("✅ Bot running in PAPER mode.")
    print("   Underlying: IBKR TWS/Gateway")
    print("   Options: Massive.com OPTIONS WebSocket\n")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        await mux.close()
        print("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
