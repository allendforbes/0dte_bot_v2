import asyncio
import logging

from bot_0dte.config import (
    EXECUTION_MODE,
    ENABLE_LIVE_TRADING,
    IB_HOST,
    IB_PORT,
    IB_CLIENT_ID,
    MARKETDATA_API_TOKEN,
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
    Clean startup/shutdown manager.
    Creates:
        - execution adapter (mock or IBKR)
        - engine
        - chain bridge
        - market data feed
        - orchestrator
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

    # -----------------------------------------------------------
    # STARTUP
    # -----------------------------------------------------------
    async def startup(self):
        self.logger.info("session.startup", {"mode": EXECUTION_MODE})
        print("\n[SESSION] Starting up…\n")

        # =======================================================
        # 1. Choose execution adapter
        # =======================================================
        if EXECUTION_MODE == "mock":
            print("[SESSION] Using MockExecAdapter")
            self.adapter = MockExecAdapter()

        elif EXECUTION_MODE == "paper":
            print("[SESSION] Using IBKRExecAdapter PAPER")
            self.adapter = IBKRExecAdapter(
                host=IB_HOST,
                port=IB_PORT,
                client_id=IB_CLIENT_ID,
                journaling_cb=None,
            )

        elif EXECUTION_MODE == "live":
            if not ENABLE_LIVE_TRADING:
                raise RuntimeError(
                    "LIVE TRADING BLOCKED. Set ENABLE_LIVE_TRADING=True in config.py"
                )
            print("[SESSION] Using IBKRExecAdapter LIVE")
            self.adapter = IBKRExecAdapter(
                host=IB_HOST,
                port=IB_PORT,
                client_id=IB_CLIENT_ID,
                journaling_cb=None,
            )

        else:
            raise ValueError(f"Unknown EXECUTION_MODE={EXECUTION_MODE}")

        # =======================================================
        # 2. Connect IBKR (not for mock)
        # =======================================================
        if EXECUTION_MODE != "mock":
            print("[SESSION] Connecting to IBKR…")
            await self.adapter.connect()
            print("[SESSION] IBKR connected.")

        # =======================================================
        # 3. ExecutionEngine
        # =======================================================
        self.engine = ExecutionEngine(use_mock=(EXECUTION_MODE == "mock"))

        if EXECUTION_MODE != "mock":
            await self.engine.attach_ib(self.adapter.ib)

        await self.engine.start()
        print("[SESSION] Execution engine started.")

        # =======================================================
        # 4. Chain Bridge
        # =======================================================
        self.chain_bridge = IBKRChainBridge(
            ib=self.adapter.ib if EXECUTION_MODE != "mock" else None,
            journaling_cb=None,
        )

        # =======================================================
        # 5. Market Data Feed
        # =======================================================
        self.feed = MarketDataFeed(api_token=MARKETDATA_API_TOKEN)

        # =======================================================
        # 6. Orchestrator
        # =======================================================
        self.orch = Orchestrator(
            engine=self.engine,
            chain_bridge=self.chain_bridge,
            feed=self.feed,
            telemetry=self.telemetry,
            logger=self.logger,
        )

        # Hook feed → orchestrator
        self.feed.callback = self.orch.on_market_data

        print("\n[SESSION] Startup complete.\n")
        self.logger.info("session.startup_complete")
        self._running = True

    # -----------------------------------------------------------
    # RUN LOOP
    # -----------------------------------------------------------
    async def run(self):
        if not self._running:
            raise RuntimeError("Session not started")

        print("[SESSION] Running…")
        self.logger.info("session.run_start")

        # Feed is started from inside the orchestrator
        await self.orch.start()

        while self._running:
            await asyncio.sleep(1)

    # -----------------------------------------------------------
    # SHUTDOWN
    # -----------------------------------------------------------
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
