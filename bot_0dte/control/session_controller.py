import asyncio
import logging

from bot_0dte.config import (
    EXECUTION_MODE,
    ENABLE_LIVE_TRADING,
    IB_HOST,
    IB_PORT,
    IB_CLIENT_ID,
    MARKETDATA_API_TOKEN,
    FEED_INTERVAL,
)

from bot_0dte.execution.adapters.ibkr_exec import IBKRExecAdapter
from bot_0dte.execution.adapters.mock_exec import MockExecAdapter

from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.data.adapters.ibkr_chain_bridge import IBKRChainBridge

from bot_0dte.data.providers.marketdata.marketdata_feed import MarketDataFeed
from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


class SessionController:
    """
    Startup/shutdown manager.
    Builds:
        • execution adapter (mock or IBKR)
        • execution engine
        • chain bridge
        • REST market data feed
        • orchestrator
    """

    def __init__(self, universe=None, mock=False):
        self.logger = StructuredLogger()
        self.universe = universe or ["SPY"]
        self.mock = mock

        self.telemetry = Telemetry()

        self.adapter = None
        self.engine = None
        self.chain_bridge = None
        self.feed = None
        self.orch = None

        self._running = False

    # ======================================================================
    # STARTUP
    # ======================================================================
    async def startup(self):
        self.logger.info("session.startup", {"mode": EXECUTION_MODE})
        print("\n[SESSION] Starting up…\n")

        # --------------------------------------------------------------
        # 1. Choose execution adapter
        # --------------------------------------------------------------
        if EXECUTION_MODE == "mock":
            print("[SESSION] Using MockExecAdapter")
            self.adapter = MockExecAdapter()

        elif EXECUTION_MODE in ("paper", "live"):
            if EXECUTION_MODE == "live" and not ENABLE_LIVE_TRADING:
                raise RuntimeError(
                    "LIVE TRADING BLOCKED. Set ENABLE_LIVE_TRADING=True in config.py"
                )

            mode_label = "LIVE" if EXECUTION_MODE == "live" else "PAPER"
            print(f"[SESSION] Using IBKRExecAdapter {mode_label}")

            self.adapter = IBKRExecAdapter(
                host=IB_HOST,
                port=IB_PORT,
                client_id=IB_CLIENT_ID,
                journaling_cb=None,
            )

        else:
            raise ValueError(f"Unknown EXECUTION_MODE={EXECUTION_MODE}")

        # --------------------------------------------------------------
        # 2. Connect IBKR unless in mock mode
        # --------------------------------------------------------------
        if EXECUTION_MODE != "mock":
            print("[SESSION] Connecting to IBKR…")
            await self.adapter.connect()
            print("[SESSION] IBKR connected.")

        # --------------------------------------------------------------
        # 3. Execution Engine
        # --------------------------------------------------------------
        self.engine = ExecutionEngine(use_mock=(EXECUTION_MODE == "mock"))

        if EXECUTION_MODE != "mock":
            await self.engine.attach_ib(self.adapter.ib)

        await self.engine.start()
        print("[SESSION] Execution engine started.")

        # --------------------------------------------------------------
        # 4. Chain Bridge
        # --------------------------------------------------------------
        self.chain_bridge = IBKRChainBridge(
            ib=self.adapter.ib if EXECUTION_MODE != "mock" else None,
            journaling_cb=None,
        )

        # --------------------------------------------------------------
        # 5. Market Data Feed (REST ONLY)
        # --------------------------------------------------------------
        self.feed = MarketDataFeed(
            callback=None,
            api_key=MARKETDATA_API_TOKEN,
            interval=FEED_INTERVAL,
        )

        # --------------------------------------------------------------
        # 6. Orchestrator
        # --------------------------------------------------------------
        self.orch = Orchestrator(
            engine=self.engine,
            chain_bridge=self.chain_bridge,
            feed=self.feed,
            telemetry=self.telemetry,
            logger=self.logger,
        )

        self.feed.callback = self.orch.on_market_data

        print("\n[SESSION] Startup complete.\n")
        self.logger.info("session.startup_complete")
        self._running = True

    # ======================================================================
    # RUN LOOP
    # ======================================================================
    async def run(self):
        if not self._running:
            raise RuntimeError("Session not started")

        print("[SESSION] Running…")
        self.logger.info("session.run_start")

        # Feed is started inside orchestrator.start()
        await self.orch.start()

        while self._running:
            await asyncio.sleep(1)

    # ======================================================================
    # SHUTDOWN
    # ======================================================================
    async def shutdown(self):
        print("\n[SESSION] Shutting down…\n")
        self._running = False

        try:
            if self.adapter and hasattr(self.adapter, "disconnect"):
                await self.adapter.disconnect()
        except Exception as e:
            self.logger.error("session.shutdown.adapter_error", {"error": str(e)})

        self.logger.info("session.shutdown_complete")
        print("[SESSION] Shutdown complete.")
