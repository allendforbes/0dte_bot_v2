"""
SessionController — WS-Native Session Management (Dec 2025 Massive Update)

Updated to use:
    • IBUnderlyingAdapter for stocks (IBKR)
    • MassiveOptionsWSAdapter (Option-A real-time Q + OQ)
    • MassiveMux v5.0 (real-time NBBO/OQ, no batching)
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

# Underlying feed (IBKR)
from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter

# NEW Massive Options WebSocket Adapter
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)

# NEW MassiveMux (v5.0, real-time)
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.universe import get_universe_for_today


logger = logging.getLogger(__name__)


class SessionController:
    """
    Updated session controller for the Massive Dec-2025 streaming upgrade.
    """

    def __init__(self, universe=None):
        self.logger = StructuredLogger()
        self.telemetry = Telemetry()

        self.universe = universe or get_universe_for_today()

        self.adapter = None
        self.engine = None
        self.stocks_ws = None
        self.options_ws = None
        self.mux = None
        self.orch = None

        self._running = False

    # ======================================================================
    async def startup(self):
        self.logger.info("session.startup", {"mode": EXECUTION_MODE})
        print("\n[SESSION] Starting WS-native bot…\n")

        # ---------------------------------------------------------
        # 1 — Execution Adapter (mock / paper / live)
        # ---------------------------------------------------------
        if EXECUTION_MODE == "mock":
            print("[SESSION] Using MockExecAdapter")
            self.adapter = MockExecAdapter()

        elif EXECUTION_MODE in ("paper", "live"):

            if EXECUTION_MODE == "live" and not ENABLE_LIVE_TRADING:
                raise RuntimeError("LIVE TRADING BLOCKED unless ENABLE_LIVE_TRADING=True")

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

        # ---------------------------------------------------------
        # 2 — Connect IBKR
        # ---------------------------------------------------------
        if EXECUTION_MODE != "mock":
            print("[SESSION] Connecting to IBKR…")
            await self.adapter.connect()
            print("[SESSION] IBKR connected.")

        # ---------------------------------------------------------
        # 3 — Execution Engine
        # ---------------------------------------------------------
        self.engine = ExecutionEngine(use_mock=(EXECUTION_MODE == "mock"))

        if EXECUTION_MODE != "mock":
            await self.engine.attach_ib(self.adapter.ib)

        await self.engine.start()
        print("[SESSION] Execution engine ready.")

        # ---------------------------------------------------------
        # 4 — Underlyings via IBKR
        # ---------------------------------------------------------
        print("[SESSION] Initializing IBUnderlyingAdapter (stocks feed)…")
        self.stocks_ws = IBUnderlyingAdapter(loop=asyncio.get_event_loop())

        # ---------------------------------------------------------
        # 5 — Options via Massive (NEW adapter)
        # ---------------------------------------------------------
        print("[SESSION] Initializing MassiveOptionsWSAdapter (options feed)…")
        self.options_ws = MassiveOptionsWSAdapter.from_env()

        # ---------------------------------------------------------
        # 6 — Combined Unified Router (MassiveMux v5.0)
        # ---------------------------------------------------------
        print("[SESSION] Creating MassiveMux (v5.0 real-time)…")
        self.mux = MassiveMux(
            ib_underlying=self.stocks_ws,
            options_ws=self.options_ws,
        )

        # ---------------------------------------------------------
        # 7 — Orchestrator
        # ---------------------------------------------------------
        print("[SESSION] Creating orchestrator…")
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
    async def run(self):
        if not self._running:
            raise RuntimeError("Session not started")

        print("[SESSION] Running bot…")
        self.logger.info("session.run_start")

        await self.orch.start()

        while self._running:
            await asyncio.sleep(1)

    # ======================================================================
    async def shutdown(self):
        print("\n[SESSION] Shutting down…\n")
        self._running = False

        try:
            if self.mux:
                await self.mux.close()

            if self.adapter and hasattr(self.adapter, "disconnect"):
                await self.adapter.disconnect()

        except Exception as e:
            self.logger.error("session.shutdown.error", {"error": str(e)})

        print("[SESSION] Shutdown complete.")
        self.logger.info("session.shutdown_complete")
