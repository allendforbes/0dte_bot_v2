# bot_0dte/control/session_controller.py
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

# NEW Massive WS providers
from bot_0dte.data.providers.massive.massive_stocks_ws_adapter import (
    MassiveStocksWSAdapter,
)
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)
from bot_0dte.data.providers.massive.massive_contract_engine import ContractEngine
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

# New orchestrator
from bot_0dte.orchestrator import Orchestrator

from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.universe import get_universe_for_today


class SessionController:
    """
    Pure WS-native session controller (Massive.com only)

    Builds:
        • execution adapter (mock or IBKR)
        • execution engine
        • Massive WS adapters (stocks + options)
        • ContractEngine (OCC auto cluster)
        • MassiveMux combined router
        • orchestrator
    """

    def __init__(self, universe=None):
        self.logger = StructuredLogger()
        self.telemetry = Telemetry()

        # If none given, load dynamic universe rules
        self.universe = universe or get_universe_for_today()

        # Components that will be built at startup
        self.adapter = None
        self.engine = None
        self.stocks_ws = None
        self.options_ws = None
        self.contract_engine = None
        self.mux = None
        self.orch = None

        self._running = False

    # ======================================================================
    # STARTUP
    # ======================================================================
    async def startup(self):
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
        # 5. Contract Engine (auto OCC cluster)
        # --------------------------------------------------------------
        self.contract_engine = ContractEngine(
            options_ws=self.options_ws,
            stocks_ws=self.stocks_ws,
            orchestrator=None,  # patched after orchestrator is created
        )

        # --------------------------------------------------------------
        # 6. Combined WS Router (MassiveMux)
        # --------------------------------------------------------------
        self.mux = MassiveMux(
            stocks_ws=self.stocks_ws,
            options_ws=self.options_ws,
            contract_engine=self.contract_engine,
        )

        # --------------------------------------------------------------
        # 7. Orchestrator (no REST, no ChainBridge)
        # --------------------------------------------------------------
        from bot_0dte.orchestrator import Orchestrator

        self.orch = Orchestrator(
            engine=self.engine,
            mux=self.mux,
            telemetry=self.telemetry,
            logger=self.logger,
            universe=self.universe,
            auto_trade_enabled=False,
            trade_mode=EXECUTION_MODE,  # "mock" → shadow, "paper", "live"
        )

        # Link orchestrator back to ContractEngine
        self.contract_engine.orch = self.orch

        print("\n[SESSION] Startup complete. Bot is ready.\n")
        self.logger.info("session.startup_complete")
        self._running = True

    # ======================================================================
    # RUN LOOP
    # ======================================================================
    async def run(self):
        if not self._running:
            raise RuntimeError("Session not started")

        print("[SESSION] Running bot…")
        self.logger.info("session.run_start")

        # orchestrator kicks off MassiveMux + WS subscriptions
        await self.orch.start()

        # Keep alive loop
        while self._running:
            await asyncio.sleep(1)

    # ======================================================================
    # SHUTDOWN
    # ======================================================================
    async def shutdown(self):
        print("\n[SESSION] Shutting down…\n")
        self._running = False

        try:
            if self.stocks_ws:
                await self.stocks_ws.close()
            if self.options_ws:
                await self.options_ws.close()
            if self.adapter and hasattr(self.adapter, "disconnect"):
                await self.adapter.disconnect()

        except Exception as e:
            self.logger.error("session.shutdown.error", {"error": str(e)})

        print("[SESSION] Shutdown complete.")
        self.logger.info("session.shutdown_complete")
