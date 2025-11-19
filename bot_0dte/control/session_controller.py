import asyncio
import logging
from typing import Optional

from bot_0dte.config import (
    EXECUTION_MODE,
    ENABLE_LIVE_TRADING,
    IB_HOST,
    IB_PORT,
    IB_CLIENT_ID,
)

from bot_0dte.execution.adapters.ibkr_exec import IBKRExecAdapter
from bot_0dte.execution.adapters.mock_exec import MockExecAdapter

from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.data.adapters.ibkr_chain_bridge import IBKRChainBridge
from bot_0dte.data.providers.marketdata.marketdata_feed import MarketDataFeed
from bot_0dte.orchestrator import HybridOrchestrator

from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


class SessionController:
    """
    Single point of startup/shutdown.
    Controls:
        - adapter creation
        - IBKR connection (adapter-owned)
        - execution engine wiring
        - chain bridge instantiation
        - orchestrator construction
        - feed startup
    """

    def __init__(self):
        self.logger = StructuredLogger()
        self.telemetry = Telemetry()

        self.adapter = None
        self.engine = None
        self.chain_bridge = None
        self.feed = None
        self.orch = None

        self._running = False

    # -------------------------------------------------------
    # STARTUP
    # -------------------------------------------------------
    async def startup(self):
        self.logger.info("session.startup", mode=EXECUTION_MODE)

        # =====================================================
        # 1. Create execution adapter (mock or real)
        # =====================================================
        if EXECUTION_MODE == "paper":
            self.adapter = IBKRExecAdapter(
                host=IB_HOST,
                port=IB_PORT,
                client_id=IB_CLIENT_ID,
                journaling_cb=self.telemetry.push,
            )

        elif EXECUTION_MODE == "live":
            if not ENABLE_LIVE_TRADING:
                raise RuntimeError(
                    "LIVE TRADING BLOCKED: set ENABLE_LIVE_TRADING=True in config.py"
                )

            self.adapter = IBKRExecAdapter(
                host=IB_HOST,
                port=IB_PORT,
                client_id=IB_CLIENT_ID,
                journaling_cb=self.telemetry.push,
            )

        else:
            raise ValueError(f"Unknown EXECUTION_MODE={EXECUTION_MODE}")

        # =====================================================
        # 2. Connect adapter (IBKR handshake)
        # =====================================================
        self.logger.info("session.adapter_connecting", mode=EXECUTION_MODE)
        await self.adapter.connect()
        self.logger.info("session.adapter_connected")

        # =====================================================
        # 3. Build ExecutionEngine (injection model)
        # =====================================================
        self.engine = ExecutionEngine(
            adapter=self.adapter,
            journaling_cb=self.telemetry.push,
        )
        await self.engine.start()

        # =====================================================
        # 4. Chain bridge (requires connected adapter)
        # =====================================================
        self.chain_bridge = IBKRChainBridge(
            ib=self.adapter.ib,
            journaling_cb=self.telemetry.push,
        )

        # =====================================================
        # 5. Market data feed
        # =====================================================
        self.feed = MarketDataFeed(
            callback=None,  # set by orchestrator
        )

        # =====================================================
        # 6. Orchestrator
        # =====================================================
        self.orch = HybridOrchestrator(
            engine=self.engine,
            chain_bridge=self.chain_bridge,
            feed=self.feed,
            telemetry=self.telemetry,
            logger=self.logger,
        )

        # Attach callback
        self.feed.callback = self.orch.on_market_data

        self._running = True

        self.logger.info("session.startup_complete")
        print("\n[SESSION] Startup complete.\n")

    # -------------------------------------------------------
    # RUN
    # -------------------------------------------------------
    async def run(self):
        if not self._running:
            raise RuntimeError("Session not started")

        self.logger.info("session.run_start")
        print("[SESSION] Running...")

        # feed.start(...) should be async create_task inside orchestrator
        await self.orch.start()

        # Keep controller alive
        while self._running:
            await asyncio.sleep(1)

    # -------------------------------------------------------
    # SHUTDOWN
    # -------------------------------------------------------
    async def shutdown(self):
        self._running = False
        self.logger.info("session.shutdown")

        try:
            if self.adapter:
                await self.adapter.disconnect()
        except Exception as e:
            self.logger.error("session.shutdown.adapter_error", error=str(e))

        print("[SESSION] Shutdown complete.")

