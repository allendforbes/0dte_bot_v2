# bot_0dte/bot_start.py
"""
Hybrid Bot Launcher (IBKR Underlying + Massive Options)
Aligned with: MassiveMux v3.4, OptionsWSAdapter v5.0, MassiveContractEngine v3.2
"""

import asyncio
import logging

from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.orchestrator import Orchestrator

from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry

logging.basicConfig(level=logging.INFO)


async def main():
    logger = StructuredLogger()
    telemetry = Telemetry()

    # ---------------------------------------------------------
    # 1. IBKR UNDERLYING FEED
    # ---------------------------------------------------------
    print("[BOOT] Initializing IBKR underlying adapter...")
    ib_underlying = IBUnderlyingAdapter(
        host="127.0.0.1",
        port=4002,
        client_id=11,
    )

    print("[BOOT] Connecting IBKR underlying...")
    await ib_underlying.connect()

    print("[BOOT] Subscribing to underlying tickers (IBKR)...")
    await ib_underlying.subscribe(["SPY", "QQQ"])

    # ⭐ CRITICAL FIX — ensure underlying ticks arrive BEFORE MassiveMux builds OCC list
    print("[BOOT] Waiting for initial underlying ticks...")
    await asyncio.sleep(0.75)   # 0.50–1.0s recommended; IBKR tick arrival varies

    # ---------------------------------------------------------
    # 2. MASSIVE OPTIONS WS
    # ---------------------------------------------------------
    print("[BOOT] Initializing Massive OPTIONS adapter...")
    options_ws = MassiveOptionsWSAdapter.from_env()

    # ---------------------------------------------------------
    # 3. MUX — Hybrid Market Data Layer
    # ---------------------------------------------------------
    print("[BOOT] Creating MassiveMux (IBKR underlying + Massive NBBO)...")
    mux = MassiveMux(
        options_ws=options_ws,       # correct argument name
        ib_underlying=ib_underlying
    )

    # ---------------------------------------------------------
    # 4. EXECUTION ENGINE
    # ---------------------------------------------------------
    print("[BOOT] Initializing ExecutionEngine (PAPER mode)...")
    engine = ExecutionEngine(use_mock=False)
    engine.ib = ib_underlying.ib
    await engine.start()

    # ---------------------------------------------------------
    # 5. ORCHESTRATOR
    # ---------------------------------------------------------
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
    print("Press Ctrl+C to stop.\n")

    # ---------------------------------------------------------
    # 6. MAIN LOOP
    # ---------------------------------------------------------
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        await mux.close()
        await ib_underlying.close()
        print("✅ Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
