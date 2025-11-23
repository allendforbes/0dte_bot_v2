# bot_start.py — Final WS-Native Bot Launcher (MassiveMux Architecture)

import asyncio
import logging

from bot_0dte.data.providers.massive.massive_stocks_ws_adapter import (
    MassiveStocksWSAdapter,
)
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)
from bot_0dte.data.providers.massive.massive_contract_engine import ContractEngine
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


logging.basicConfig(level=logging.INFO)


async def main():
    logger = StructuredLogger()
    telemetry = Telemetry()

    # ------------------------------
    # 1. WebSocket adapters
    # ------------------------------
    stocks_ws = MassiveStocksWSAdapter.from_env()
    options_ws = MassiveOptionsWSAdapter.from_env()

    # ------------------------------
    # 2. MUX (routes all events)
    # ------------------------------
    mux = MassiveMux(stocks_ws=stocks_ws, options_ws=options_ws)

    # ------------------------------
    # 3. Execution engine (mock by default)
    # ------------------------------
    engine = ExecutionEngine(use_mock=True)

    # ------------------------------
    # 4. Orchestrator
    # ------------------------------
    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=telemetry,
        logger=logger,
        auto_trade_enabled=False,
        trade_mode="shadow",
    )

    # ------------------------------
    # 5. ContractEngine (dynamic OCC subscriptions)
    # ------------------------------
    contract_engine = ContractEngine(
        options_ws=options_ws,
        stocks_ws=stocks_ws,
        orchestrator=orch,
    )

    # Register underlying updates
    stocks_ws.on_underlying(contract_engine.on_underlying)

    # ------------------------------
    # START EVERYTHING
    # ------------------------------
    print("\n🚀 Starting bot...\n")
    await orch.start()

    # Keep alive forever
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
