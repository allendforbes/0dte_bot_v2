"""
SessionController — WS-Native Session Management

Optional wrapper for bot_start.py that adds:
    • IBKR connection management
    • Execution mode switching (mock/paper/live)
    • Graceful startup/shutdown

Note: bot_start.py can launch directly without this.
This is kept for convenience and IBKR integration.
"""

import asyncio
import logging

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

from bot_0dte.data.providers.massive.massive_stocks_ws_adapter import (
    MassiveStocksWSAdapter,
)
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

from bot_0dte.orchestrator import Orchestrator

from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.universe import get_universe_for_today


logger = logging.getLogger(__name__)


class SessionController:
    """
    WS-native session controller.

    Manages:
        • Execution adapter lifecycle (IBKR or mock)
        • WebSocket connections
        • Orchestrator lifecycle
        • Graceful shutdown
    """

    def __init__(self, universe=None):
        self.logger = StructuredLogger()
        self.telemetry = Telemetry()

        # Universe
        self.universe = universe or get_universe_for_today()

        # Components
        self.adapter = None
        self.engine = None
        self.stocks_ws = None
        self.options_ws = None
        self.mux = None
        self.orch = None

        self._running = False

    # ======================================================================
    # STARTUP
    # ======================================================================
    async def startup(self):
        """
        Initialize all components and connect.
        """
        self.logger.info("session.startup", {"mode": EXECUTION_MODE})
        print("\n[SESSION] Starting WS-native bot…\n")

        # --------------------------------------------------------------
        # 1. Choose execution adapter
        # --------------------------------------------------------------
        if EXECUTION_MODE == "mock":
            print("[SESSION] Using MockExecAdapter")
            self.adapter = MockExecAdapter()

        elif EXECUTION_MODE in ("paper", "live"):

            if EXECUTION_MODE == "live" and not ENABLE_LIVE_TRADING:
                raise RuntimeError(
                    "LIVE TRADING BLOCKED unless ENABLE_LIVE_TRADING=True"
                )

            mode_label = "LIVE" if EXECUTION_MODE == "live" else "PAPER"
            print(f"[SESSION] Using IBKRExecAdapter ({mode_label})")

            self.adapter = IBKRExecAdapter(
                host=IB_HOST,
                port=IB_PORT,
                client_id=IB_CLIENT_ID,
                journaling_cb=None,
            )
        else:
            raise ValueError(f"Unknown EXECUTION_MODE={EXECUTION_MODE}")

        # --------------------------------------------------------------
        # 2. Connect IBKR only if not mock
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
        print("[SESSION] Execution engine ready.")

        # --------------------------------------------------------------
        # 4. Massive WebSocket Adapters
        # --------------------------------------------------------------
        print("[SESSION] Initializing Massive WS adapters…")

        self.stocks_ws = MassiveStocksWSAdapter.from_env()
        self.options_ws = MassiveOptionsWSAdapter.from_env()

        # --------------------------------------------------------------
        # 5. MassiveMux (WS router)
        # --------------------------------------------------------------
        print("[SESSION] Creating MassiveMux...")
        self.mux = MassiveMux(stocks_ws=self.stocks_ws, options_ws=self.options_ws)

        # --------------------------------------------------------------
        # 6. Orchestrator (WS-native, no REST, no chain bridge)
        # --------------------------------------------------------------
        print("[SESSION] Creating orchestrator...")
        self.orch = Orchestrator(
            engine=self.engine,
            mux=self.mux,
            telemetry=self.telemetry,
            logger=self.logger,
            universe=self.universe,
            auto_trade_enabled=False,
            trade_mode=EXECUTION_MODE,
        )

        print("\n[SESSION] Startup complete. Bot is ready.\n")
        self.logger.info("session.startup_complete")
        self._running = True

    # ======================================================================
    # RUN LOOP
    # ======================================================================
    async def run(self):
        """
        Start orchestrator and run until stopped.
        """
        if not self._running:
            raise RuntimeError("Session not started")

        print("[SESSION] Running bot…")
        self.logger.info("session.run_start")

        # Start orchestrator (connects WS, subscribes symbols)
        await self.orch.start()

        # Keep alive loop
        while self._running:
            await asyncio.sleep(1)

    # ======================================================================
    # SHUTDOWN
    # ======================================================================
    async def shutdown(self):
        """
        Gracefully shutdown all components.
        """
        print("\n[SESSION] Shutting down…\n")
        self._running = False

        try:
            # Close WebSockets
            if self.mux:
                await self.mux.close()

            # Disconnect IBKR
            if self.adapter and hasattr(self.adapter, "disconnect"):
                await self.adapter.disconnect()

        except Exception as e:
            self.logger.error("session.shutdown.error", {"error": str(e)})

        print("[SESSION] Shutdown complete.")
        self.logger.info("session.shutdown_complete")
