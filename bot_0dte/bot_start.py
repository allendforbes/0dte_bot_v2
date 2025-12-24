# bot_0dte/bot_start.py
"""
Hybrid Bot Launcher (IBKR Underlying + Massive Options)
ASCII UI - Rich completely removed
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
from bot_0dte.infra.phase import ExecutionPhase

logging.basicConfig(level=logging.INFO)


async def main():
    # ========================================
    # PHASE: Resolve from environment
    # ========================================
    execution_phase = ExecutionPhase.from_env(default="shadow")
    
    print("\n" + "=" * 70)
    print(f" BOOTING IN {execution_phase.value.upper()} MODE ".center(70, "="))
    print("=" * 70 + "\n")
    
    logger = StructuredLogger()
    telemetry = Telemetry()

    # ---------------------------------------------------------
    # 1. IBKR UNDERLYING FEED (All modes for market data)
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

    print("[BOOT] Waiting for initial underlying ticks...")
    await asyncio.sleep(0.75)

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
        options_ws=options_ws,
        ib_underlying=ib_underlying,
    )

    # ---------------------------------------------------------
    # 4. EXECUTION ENGINE
    # ---------------------------------------------------------
    print("[BOOT] Initializing ExecutionEngine...")
    engine = ExecutionEngine(
        use_mock=(execution_phase == ExecutionPhase.SHADOW),
        execution_phase=execution_phase,
    )

    # ---------------------------------------------------------
    # OPTIONAL SAFETY SELF-TEST (SHADOW ONLY)
    # ---------------------------------------------------------
    if execution_phase == ExecutionPhase.SHADOW:
        try:
            await engine.send_bracket(
                symbol="SPY",
                side="CALL",
                qty=1,
                entry_price=0.01,
                take_profit=0.02,
                stop_loss=0.005,
                meta={"strike": 0},
            )
            raise AssertionError("FATAL: SHADOW execution did not raise")
        except RuntimeError:
            print("[BOOT] SHADOW execution guard verified.")

    # ---------------------------------------------------------
    # START ENGINE (Paper / Live only)
    # ---------------------------------------------------------
    if execution_phase in (ExecutionPhase.PAPER, ExecutionPhase.LIVE):
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
        execution_phase=execution_phase,  # enum, not string
    )

    # ---------------------------------------------------------
    # 6. START ORCHESTRATOR
    # ---------------------------------------------------------
    print("[BOOT] Starting orchestrator...")
    await orch.start()

    # ---------------------------------------------------------
    # 7. WAIT FOR SHUTDOWN SIGNAL
    # ---------------------------------------------------------
    if orch._shutdown is None:
        print("❌ FATAL: _shutdown Event was not created in start()")
        return
    
    await orch._shutdown.wait()

    print("\n🛑 Shutdown signal received. Cleaning up…")

    # Orchestrator cleanup
    await orch.shutdown()

    # Close data feeds
    try:
        await mux.close()
    except Exception:
        pass

    if ib_underlying:
        try:
            await ib_underlying.close()
        except Exception:
            pass

    print("✅ Shutdown complete.")


# ---------------------------------------------------------
# ENTRYPOINT
# ---------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\033[?25h")
        print("Force exit.")