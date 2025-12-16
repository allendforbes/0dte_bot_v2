import os
import time
import asyncio
import signal
import contextlib
from typing import Dict, Any, List

from datetime import datetime, time as dttime
import pytz

# Strategy components
from bot_0dte.strategy.elite_entry_diagnostic import EliteEntryEngine
from bot_0dte.strategy.elite_latency_precheck import EliteLatencyPrecheck
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.strategy.continuation_engine import ContinuationEngine

# Risk
from bot_0dte.risk.trail_logic import TrailLogic

# Chain & Greeks
from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.data.providers.massive.massive_rest_snapshot_client import MassiveSnapshotClient
from bot_0dte.chain.greek_injector import GreekInjector

# UI
from rich.console import Console
from bot_0dte.ui.dashboard import LiveDashboard
from bot_0dte.ui.market_state import MarketStatePublisher

# Infra
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.infra.phase import ExecutionPhase
from bot_0dte.infra.decision_logger import DecisionLogger, ConvexityLogger


# ======================================================================
# VWAP TRACKER
# ======================================================================
class VWAPTracker:
    def __init__(self, window_size=100):
        self.window_size = window_size
        self.prices = []
        self.volumes = []
        self.last_dev = 0.0

    def update(self, price: float, volume: float = 1.0):
        self.prices.append(price)
        self.volumes.append(volume)

        if len(self.prices) > self.window_size:
            self.prices.pop(0)
            self.volumes.pop(0)

        total_pv = sum(p*v for p, v in zip(self.prices, self.volumes))
        total_v  = sum(self.volumes)

        vwap = total_pv / total_v if total_v else price
        dev = price - vwap
        change = dev - self.last_dev
        self.last_dev = dev

        return {
            "vwap": vwap,
            "vwap_dev": dev,
            "vwap_dev_change": change
        }


# ======================================================================
# MICROSTRUCTURE HELPERS
# ======================================================================
def compute_upvol_pct(rows):
    if not rows:
        return None
    call_vol = sum(r.get("volume") or 0 for r in rows if r["right"] == "C")
    put_vol  = sum(r.get("volume") or 0 for r in rows if r["right"] == "P")
    tot = call_vol + put_vol
    return None if tot == 0 else 100 * call_vol / tot


def compute_flow_ratio(rows):
    calls = [r["premium"] for r in rows if r["right"] == "C" and r["premium"]]
    puts  = [r["premium"] for r in rows if r["right"] == "P" and r["premium"]]
    if not calls or not puts:
        return None
    put_avg = sum(puts) / len(puts)
    return None if put_avg == 0 else (sum(calls) / len(calls)) / put_avg


def compute_iv_change(rows):
    ivs = [r.get("iv") for r in rows if r["right"] == "C" and r.get("iv")]
    if not ivs:
        return None
    return 0.0 if len(ivs) < 2 else ivs[-1] - (sum(ivs) / len(ivs))


def compute_skew_shift(rows):
    calls = [r.get("iv") for r in rows if r["right"] == "C" and r.get("iv")]
    puts  = [r.get("iv") for r in rows if r["right"] == "P" and r.get("iv")]
    if not calls or not puts:
        return None
    return (sum(calls)/len(calls)) - (sum(puts)/len(puts))

# ======================================================================
# ORCHESTRATOR
# ======================================================================
class Orchestrator:

    RISK_PCT = 0.05
    CONTRACT_CAPS = {
        "SPY": 20, "QQQ": 20,
        "AAPL": 10, "AMZN": 10, "META": 10,
        "MSFT": 10, "NVDA": 10, "TSLA": 10
    }
    DEFAULT_CAP = 5

    def __init__(
        self,
        engine,
        mux,
        telemetry: Telemetry,
        logger: StructuredLogger,
        universe=None,
        auto_trade_enabled=False,
        execution_phase: ExecutionPhase = None,
    ):
        # -------------------------------------------------
        # Phase resolution
        # -------------------------------------------------
        if execution_phase is None:
            execution_phase = ExecutionPhase.from_env(default="shadow")
        
        self.execution_phase = execution_phase
        
        print("\n" + "=" * 70)
        print(f" EXECUTION PHASE: {self.execution_phase.value.upper()} ".center(70, "="))
        print("=" * 70 + "\n")
        
        # -------------------------------------------------
        # Core
        # -------------------------------------------------
        self.engine = engine
        self.mux = mux
        self.logger = logger
        self.telemetry = telemetry
        self.auto = auto_trade_enabled
        
        # -------------------------------------------------
        # Decision + Convexity Loggers
        # -------------------------------------------------
        self.decision_log = DecisionLogger(self.execution_phase.value)
        self.convexity_log = ConvexityLogger(self.execution_phase.value)

        # -------------------------------------------------
        # Universe
        # -------------------------------------------------
        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        # -------------------------------------------------
        # Underlying tracking
        # -------------------------------------------------
        self.last_price = {s: None for s in self.symbols}
        self.vwap = {s: VWAPTracker() for s in self.symbols}

        # -------------------------------------------------
        # UI market state publisher
        # -------------------------------------------------
        self.market_state = MarketStatePublisher(self.symbols)

        # -------------------------------------------------
        # Chain aggregation + freshness
        # -------------------------------------------------
        self.chain_agg = ChainAggregator(self.symbols)
        self.freshness = None

        # -------------------------------------------------
        # Massive snapshot + Greeks
        # -------------------------------------------------
        self.snapshot_client = MassiveSnapshotClient(
            api_key=os.getenv("MASSIVE_API_KEY")
        )
        self.greek_injector = GreekInjector(self.snapshot_client)

        # -------------------------------------------------
        # Strategy engines
        # -------------------------------------------------
        self.entry_engine = EliteEntryEngine()
        self.latency = EliteLatencyPrecheck()
        self.selector = StrikeSelector()
        self.trail = TrailLogic(max_loss_pct=0.50)
        self.continuation = ContinuationEngine()

        # -------------------------------------------------
        # Active trade state
        # -------------------------------------------------
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None

        # -------------------------------------------------
        # Dashboard
        # -------------------------------------------------
        self.console = Console()
        self.dashboard = LiveDashboard(
            self.console,
            self.market_state,
            execution_phase=self.execution_phase.value
        )

        # -------------------------------------------------
        # Market clock
        # -------------------------------------------------
        self._tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(self._tz)
        self._session_open_dt = self._tz.localize(
            datetime.combine(now_et.date(), dttime(9, 30))
        )

        # -------------------------------------------------
        # Shutdown coordination
        # -------------------------------------------------
        self._shutdown = None
        self._tasks: list[asyncio.Task] = []

        print("\n" + "=" * 70)
        print(" ELITE ORCHESTRATOR (A2-M / FreshnessV2 / Sizing v5.0) ".center(70, "="))
        print("=" * 70 + "\n")


    # ------------------------------------------------------------
    def track(self, task: asyncio.Task):
        self._tasks.append(task)
        return task

    # ------------------------------------------------------------
    async def start(self):
        """Start market data streams + hydration."""
        import time
        
        print("[START] Creating asyncio.Event()")
        t0 = time.monotonic()
        if self._shutdown is None:
            self._shutdown = asyncio.Event()
        print(f"[OK] asyncio.Event() in {time.monotonic() - t0:.3f}s")
        
        print("[START] Registering signal handlers")
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: self._shutdown.set())
            except NotImplementedError:
                pass
        print(f"[OK] Signal handlers in {time.monotonic() - t0:.3f}s")
        
        print("[START] Setting up mux callbacks")
        
        print("[START] BEFORE mux.options.set_parent")
        t0 = time.monotonic()
        self.mux.options.set_parent(self)
        print(f"[OK] AFTER mux.options.set_parent in {time.monotonic() - t0:.3f}s")
        
        print("[START] BEFORE mux.on_underlying")
        t0 = time.monotonic()
        self.mux.on_underlying(self._on_underlying)
        print(f"[OK] AFTER mux.on_underlying in {time.monotonic() - t0:.3f}s")
        
        print("[START] BEFORE mux.on_option")
        t0 = time.monotonic()
        self.mux.on_option(self._on_option)
        print(f"[OK] AFTER mux.on_option in {time.monotonic() - t0:.3f}s")
        
        print("[START] BEFORE mux.set_parent")
        t0 = time.monotonic()
        self.mux.set_parent(self)
        print(f"[OK] AFTER mux.set_parent in {time.monotonic() - t0:.3f}s")

        print("[START] mux.connect()")
        t0 = time.monotonic()
        task = asyncio.create_task(self.mux.connect(self.symbols, self.expiry_map))
        self.track(task)
        await task
        print(f"[OK] mux.connect() in {time.monotonic() - t0:.2f}s")

        print("[START] fetch_snapshot_and_hydrate()")
        t0 = time.monotonic()
        await self.mux.fetch_snapshot_and_hydrate(self.chain_agg)
        print(f"[OK] fetch_snapshot_and_hydrate() in {time.monotonic() - t0:.2f}s")
        
        print("[START] Assigning freshness")
        t0 = time.monotonic()
        self.freshness = self.mux.freshness
        print(f"[OK] Freshness assigned in {time.monotonic() - t0:.3f}s")
        
        print("[ORCHESTRATOR] start() completed successfully")

    # ------------------------------------------------------------
    async def _on_underlying(self, event):
        sym = event.get("symbol")
        price = event.get("price")
        if sym not in self.symbols or price is None:
            return

        if self.freshness and sym in self.freshness:
            try:
                self.freshness[sym].update(int(time.time() * 1000))
            except:
                pass

        self.last_price[sym] = price
        self.market_state.update_price(sym, price)

        await self._evaluate(sym, price)

    # ------------------------------------------------------------
    async def _on_option(self, event):
        sym = event.get("symbol")
        if sym not in self.symbols:
            return

        self.chain_agg.update_from_nbbo(event)

        bid = event.get("bid")
        ask = event.get("ask")
        
        self.market_state.update_nbbo(sym, bid=bid, ask=ask)

        if not self.trail.state.active:
            return

        if event.get("contract") != self.active_contract:
            return

        if bid is None or ask is None:
            return

        mid = (bid + ask) / 2

        if mid <= self.active_entry_price * 0.50:
            await self._execute_exit(sym, "hard_sl")
            return

        trail_res = self.trail.update(sym, mid)
        if trail_res.get("should_exit"):
            await self._execute_exit(sym, "trail_exit")

    # ------------------------------------------------------------
    async def _evaluate(self, symbol: str, price: float):
        pass

    # ------------------------------------------------------------
    async def _execute_exit(self, symbol: str, reason: str):
        pass

    # ------------------------------------------------------------
    async def shutdown(self):
        """Coordinated shutdown: cancel tasks, close data, restore UI."""
        print("[SYS] Shutdown requested…")

        try:
            self.decision_log.close()
            self.convexity_log.close()
        except:
            pass

        for t in self._tasks:
            if not t.done():
                t.cancel()

        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(*self._tasks, return_exceptions=True)

        print("[SYS] Background tasks cancelled.")

        if hasattr(self.mux, "shutdown"):
            try:
                await self.mux.shutdown()
                print("[SYS] Mux shutdown complete.")
            except Exception as e:
                print(f"[SYS] Mux shutdown error: {e}")

        if hasattr(self.selector, "shutdown"):
            try:
                await self.selector.shutdown()
            except:
                pass

        with contextlib.suppress(Exception):
            self.trail.stop()

        try:
            self.dashboard.stop()
        except:
            print("\033[?25h")

        print("[SYS] Shutdown complete.")