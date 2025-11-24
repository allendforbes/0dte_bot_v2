"""
bot_start.py — WS-Native Bot Launcher (PAPER-MODE READY)
Fully wired for:
    • Massive WS (stocks + options)
    • ContractEngine OCC subscription management
    • Full orchestrator pipeline (VWAP + breakout + strike select)
    • Auto-reconnect
"""

import asyncio
import logging

from bot_0dte.data.providers.massive.massive_stocks_ws_adapter import (
    MassiveStocksWSAdapter,
)
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

from bot_0dte.data.providers.massive.massive_contract_engine import ContractEngine
from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


logging.basicConfig(level=logging.INFO)


async def main():
    logger = StructuredLogger()
    telemetry = Telemetry()

    print("[BOOT] Initializing WebSocket adapters...")
    stocks_ws = MassiveStocksWSAdapter.from_env()
    options_ws = MassiveOptionsWSAdapter.from_env()

    print("[BOOT] Creating MassiveMux...")
    mux = MassiveMux(stocks_ws=stocks_ws, options_ws=options_ws)

    print("[BOOT] Initializing execution engine (PAPER mode)...")
    engine = ExecutionEngine(use_mock=False)

    await engine.start()

    # Universe + expiry comes from orchestrator
    # but ContractEngine needs expiry, so we initialize it after orch
    print("[BOOT] Creating orchestrator...")
    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        auto_trade_enabled=True,  # ENABLE TRADING
        trade_mode="paper",  # <<< PAPER MODE
    )

    # ------------------------------
    # ContractEngine — OCC subscriptions
    # ------------------------------
    print("[BOOT] Initializing ContractEngine...")
    contract_engine = ContractEngine(
        options_ws=options_ws,
        expiry_map=orch.expiry_map,
    )

    # Wire underlying feed → ContractEngine
    mux.on_underlying(contract_engine.on_underlying)

    print("[BOOT] Initializing execution engine (PAPER mode)...")
    engine = ExecutionEngine(use_mock=False)  # ← Connects to IB gateway in paper mode
    await engine.start()

    # ------------------------------
    # Start orchestrator (connect WS)
    # ------------------------------
    print("\n🚀 Starting WS-native bot (PAPER MODE)...\n")
    await orch.start()

    print("✅ Bot running. Press Ctrl+C to stop.\n")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        await mux.close()
        print("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
