"""
0DTE Options Trading Orchestrator

⚠️ DEV MODE: FORCE_SHADOW_ENTRY
When FORCE_SHADOW_ENTRY=1 is set, the system will:
- Fire a single forced trade in SHADOW mode only
- Bypass strike selection and use synthetic values
- Auto-exit after 30 seconds with synthetic price movement
- Test trail logic, R-multiple math, and UI transitions

This is for mechanics testing ONLY, not expectancy inference.
To disable: simply don't set FORCE_SHADOW_ENTRY environment variable.
"""

import os
import time
import asyncio
import signal
import contextlib
from typing import Dict, Any, List

from datetime import datetime, time as dttime
import pytz

# Strategy (data-only)
from bot_0dte.strategy.elite_entry import EliteEntryEngine, EliteSignal
from bot_0dte.strategy.strike_selector import StrikeSelector

# Risk
from bot_0dte.risk.trail_logic import TrailLogic

# Chain & data
from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.data.providers.massive.massive_rest_snapshot_client import MassiveSnapshotClient

# ASCII UI (snapshot-based, no Rich)
from bot_0dte.infra.ui_snapshot import build_ui_snapshot
from bot_0dte.infra.ui_clock import UiClock
from bot_0dte.ui.ascii_renderer import render

# Infra
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.infra.phase import ExecutionPhase
from bot_0dte.infra.trading_phase import TradingPhase
from bot_0dte.infra.decision_logger import DecisionLogger, ConvexityLogger

# --------------------------------------------------
# DEV FLAGS (module-level, evaluated once at startup)
# --------------------------------------------------
FORCE_SHADOW_ENTRY = os.getenv("FORCE_SHADOW_ENTRY") == "1"

# ======================================================================
# VWAP TRACKER
# ======================================================================
class VWAPTracker:
    def __init__(self, window_size=100):
        self.window_size = window_size
        self.prices = []
        self.volumes = []
        self.last_dev = 0.0
        self.last_vwap = None
        self.last_dev_change = 0.0

    @property
    def current(self):
        """Read-only accessor for UI snapshot (no mutation)."""
        return {
            "vwap": self.last_vwap,
            "dev": self.last_dev,
            "dev_change": self.last_dev_change,
        }

    def update(self, price: float, volume: float = 1.0):
        self.prices.append(price)
        self.volumes.append(volume)

        if len(self.prices) > self.window_size:
            self.prices.pop(0)
            self.volumes.pop(0)

        total_pv = sum(p*v for p, v in zip(self.prices, self.volumes))
        total_v  = sum(self.volumes)

        vwap = total_pv / total_v if total_v else price
        
        # Raw deviation: price - vwap
        dev = price - vwap
        change = dev - self.last_dev
        
        # Cache for read-only access
        self.last_vwap = vwap
        self.last_dev = dev
        self.last_dev_change = change

        return {
            "vwap": vwap,
            "vwap_dev": dev,
            "vwap_dev_change": change
        }


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
        # Phase resolution
        if execution_phase is None:
            execution_phase = ExecutionPhase.from_env(default="shadow")
        
        self.execution_phase = execution_phase

        print("\n" + "=" * 70)
        print(f" EXECUTION PHASE: {self.execution_phase.value.upper()} ".center(70, "="))
        print("=" * 70 + "\n")

        # -------------------------------------------------
        # Runtime dev controls (SHADOW ONLY)
        # -------------------------------------------------
        import os
        self.force_shadow_entry = os.getenv("FORCE_SHADOW_ENTRY") == "1"
        self._forced_entry_fired = False

        if self.force_shadow_entry:
            logger.info("[DEV] FORCE_SHADOW_ENTRY ENABLED")
        
        # Core
        self.engine = engine
        self.mux = mux
        self.logger = logger
        self.telemetry = telemetry
        self.auto = auto_trade_enabled

        # ----------------------------------
        # DEV / TEST STATE
        # ----------------------------------
        self._forced_entry_fired = False
        
        # -------------------------------------------------
        # Market session timing
        # -------------------------------------------------
        self._market_open_ts = None
        
        # Decision + Convexity Loggers
        self.decision_log = DecisionLogger(self.execution_phase.value)
        self.convexity_log = ConvexityLogger(self.execution_phase.value)

        # Universe
        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        # Underlying tracking
        self.last_price = {s: None for s in self.symbols}
        self.vwap = {s: VWAPTracker() for s in self.symbols}

        # Chain aggregation + freshness
        self.chain_agg = ChainAggregator(self.symbols)
        self.freshness = None

        # Massive snapshot + Greeks
        self.snapshot_client = MassiveSnapshotClient(
            api_key=os.getenv("MASSIVE_API_KEY")
        )

        # Strategy engines (minimal)
        self.entry_engine = EliteEntryEngine()
        self.selector = StrikeSelector()
        self.trail = TrailLogic(max_loss_pct=0.50)

        # Active trade state
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None
        self.active_grade = None
        self.active_score = None
        
        # Acceptance state tracking (per symbol, orchestrator-owned)
        self.acceptance_state = {
            s: {
                "hold_bars": 0,
                "range_high": None,
                "range_low": None,
                "bias": None,
                "regime": None,
            }
            for s in self.symbols
        }
        
        # Post-exit micro cooldown (30 seconds, mechanical only)
        self.last_exit_ts = None
        
        # Trading phase (PRE/IN/POST)
        self.trading_phase = TradingPhase.PRE_TRADE
        self._post_trade_ts = None
        self.last_trade_view = None
        
        # Hydration state
        self.hydration_complete = False

        # ASCII UI components (snapshot-based, throttled)
        self.ui_clock = UiClock(hz=5.0)

        # Market clock
        self._tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(self._tz)
        self._session_open_dt = self._tz.localize(
            datetime.combine(now_et.date(), dttime(9, 30))
        )

        # Shutdown coordination
        self._shutdown = None
        self._tasks: list[asyncio.Task] = []
        self._shutdown_created = False

        print("\n" + "=" * 70)
        print(" ASCII UI ORCHESTRATOR (Rich Removed) ".center(70, "="))
        print("=" * 70 + "\n")

    def track(self, task: asyncio.Task):
        self._tasks.append(task)
        return task

    def _resolve_market_open_ts(self) -> float:
        """
        Resolve market open timestamp in monotonic time.
        Safe for SHADOW mode and pre-market.
        """
        try:
            tz = pytz.timezone("US/Eastern")
            now = datetime.now(tz)

            open_dt = now.replace(
                hour=9, minute=30, second=0, microsecond=0
            )

            now_mono = time.monotonic()

            # If before market open, treat open as now
            if now < open_dt:
                return now_mono

            seconds_since_open = (now - open_dt).total_seconds()
            return now_mono - seconds_since_open

        except Exception:
            # Fail-safe: avoid crashing the orchestrator
            return time.monotonic()

    @property
    def trade_view(self):
        """Read-only view of active trade for UI snapshot."""
        if self.active_symbol is None:
            return None

        return {
            "symbol": self.active_symbol,
            "bias": self.active_bias,
            "contract": self.active_contract,
            "entry": self.active_entry_price,
            "qty": self.active_qty,
            "grade": self.active_grade,
            "score": self.active_score,

            "trail": getattr(self.trail.state, "trail_level", 0.0),
            "trail_active": bool(self.trail.state.active),
            "oneR": getattr(self.trail.state, "oneR", 0.0),
        }

    @property
    def seconds_since_open(self) -> float:
        if not self._market_open_ts:
            return 0.0
        return max(0.0, time.monotonic() - self._market_open_ts)

    async def start(self):
        """Start market data streams."""
        if self._shutdown is None:
            self._shutdown = asyncio.Event()
            self._shutdown_created = True
        
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: self._shutdown.set())
            except NotImplementedError:
                pass
        
        self.mux.on_underlying(self._on_underlying)
        self.mux.on_option(self._on_option)
        
        task = asyncio.create_task(self.mux.connect(self.symbols, self.expiry_map))
        self.track(task)
        await task
        
        self._market_open_ts = self._resolve_market_open_ts()
        
        self.hydration_complete = True
        self.freshness = self.mux.freshness
        
        print("[ORCHESTRATOR] Start complete")

    async def _on_underlying(self, event):
        """
        Handle underlying price tick.
        """
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
        
        # POST_TRADE fade-back (4 second display)
        if self.trading_phase == TradingPhase.POST_TRADE:
            if self._post_trade_ts and time.monotonic() - self._post_trade_ts > 4.0:
                self.trading_phase = TradingPhase.PRE_TRADE
                self.last_trade_view = None
        
        # UI refresh (throttled)
        if self.ui_clock.ready():
            snap = build_ui_snapshot(self)
            render(snap)

        await self._evaluate(sym, price)

    async def _on_option(self, event):
        """
        Handle option NBBO tick.
        """
        sym = event.get("symbol")
        if sym not in self.symbols:
            return

        # Always update chain (strategy needs this)
        self.chain_agg.update_from_nbbo(event)

        # Trail management for active contract
        if self.active_contract and event.get("contract") == self.active_contract:
            bid = event.get("bid")
            ask = event.get("ask")
            
            if bid is not None and ask is not None:
                mid_price = (bid + ask) / 2
                
                # Update trail
                if self.trail.state.active:
                    self.trail.update(sym, mid_price)
                    
                    # Check for trail exit
                    if mid_price <= self.trail.state.trail_level:
                        await self._execute_exit(sym, reason="trail_stop")

    async def _evaluate(self, symbol: str, price: float):
        """
        Strategy evaluation on underlying tick.
        """
        
        if not self.hydration_complete:
            return
        
        # Skip if trade active
        if self.active_symbol is not None:
            await self._manage_trade(symbol, price)
            return

        # --------------------------------------------------
        # DEV: Runtime-controlled forced SHADOW entry
        # --------------------------------------------------
        if (
            FORCE_SHADOW_ENTRY
            and self.execution_phase.name == "SHADOW"
            and self.trading_phase.name == "PRE_TRADE"
            and symbol == "SPY"
            and not self._forced_entry_fired
        ):
            self._forced_entry_fired = True

            # ⚠️ DEV-ONLY: Synthetic strike bypasses selector and liquidity checks
            # This is deterministic test data, not real market conditions
            synthetic_strike = {
                "strike": price + 5.0,  # synthetic strike
                "contract": "SPY_FORCED_TEST",
                "premium": 1.00,  # fixed $1.00 entry
            }
            
            # Synthetic signal
            signal = EliteSignal(
                bias="CALL",
                grade="A",
                regime="FORCED",
                score=0.0,
                trail_mult=1.30,
            )

            self.logger.log_event("forced_shadow_entry", {"forced": True})
            
            # Use normal entry path with synthetic values
            await self._execute_entry(symbol, signal, synthetic_strike, qty=1)
            return
        
        # --------------------------------------------------
        # Normal auto-trade gating
        # --------------------------------------------------

        if not self.auto:
            return
        
        # Post-exit micro cooldown (30 seconds, mechanical only)
        if self.last_exit_ts is not None:
            if time.monotonic() - self.last_exit_ts < 30.0:
                return
        
        # Get chain
        chain_rows = self.chain_agg.get_chain(symbol)
        if not chain_rows:
            return
        
        # VWAP data
        vwap_data = self.vwap[symbol].update(price)
        
        # Market snapshot (structure-only)
        snap = {
            "symbol": symbol,
            "price": price,
            "vwap": vwap_data.get("vwap"),
            "vwap_dev": vwap_data.get("vwap_dev"),
            "vwap_dev_change": vwap_data.get("vwap_dev_change"),
            "seconds_since_open": self.seconds_since_open,
        }

        # ----------------------------------------------------------------
        # REGIME DETECTION
        # ----------------------------------------------------------------
        try:
            regime = self.entry_engine.detect_regime(snap)
        except Exception:
            self.logger.exception("[ENTRY] detect_regime() failed")
            return
        
        if not regime:
            # Pinned at VWAP - reset acceptance state
            self.acceptance_state[symbol] = {
                "hold_bars": 0,
                "range_high": None,
                "range_low": None,
                "bias": None,
                "regime": None,
            }
            return
        
        # ----------------------------------------------------------------
        # ACCEPTANCE STATE MANAGEMENT (orchestrator-owned)
        # ----------------------------------------------------------------
        state = self.acceptance_state[symbol]

        # Reset acceptance ONLY on bias flip (not regime transitions)
        if state["bias"] != regime.bias:
            state["hold_bars"] = 0
            state["range_high"] = price
            state["range_low"] = price
            state["bias"] = regime.bias
            state["regime"] = regime.regime
            state["last_hold_ts"] = 0
        else:
            # Update regime without resetting acceptance
            state["regime"] = regime.regime

        # ---------------------------------------------------------------
        # HOLD BAR TRACKING (TIME-BASED, NOT TICK-BASED)
        # ---------------------------------------------------------------
        vwap = snap.get("vwap")
        now = snap.get("timestamp")

        if "last_hold_ts" not in state:
            state["last_hold_ts"] = 0

        HOLD_INTERVAL = 5  # seconds ≈ one bar

        if vwap is not None:
            if regime.bias == "CALL" and price > vwap:
                if now - state["last_hold_ts"] >= HOLD_INTERVAL:
                    state["hold_bars"] += 1
                    state["last_hold_ts"] = now

            elif regime.bias == "PUT" and price < vwap:
                if now - state["last_hold_ts"] >= HOLD_INTERVAL:
                    state["hold_bars"] += 1
                    state["last_hold_ts"] = now

            else:
                state["hold_bars"] = 0
                state["last_hold_ts"] = 0

        # ---------------------------------------------------------------
        # RANGE TRACKING (INDEPENDENT OF HOLD)
        # ---------------------------------------------------------------
        if state["range_high"] is None or price > state["range_high"]:
            state["range_high"] = price

        if state["range_low"] is None or price < state["range_low"]:
            state["range_low"] = price

        
        # ----------------------------------------------------------------
        # TREND-FIRST PRIORITY ROUTING
        # ----------------------------------------------------------------
        signal = None
        
        # PRIORITY 1: TREND (daily participation, enabled from 9:35+)
        TREND_SESSION_START = 300  # 5 minutes after open
        
        if regime.regime == "TREND" and self.seconds_since_open >= TREND_SESSION_START:
            try:
                if self.entry_engine.acceptance_ok(snap, state):
                    signal = self.entry_engine.build_signal(regime, snap)
            except Exception:
                self.logger.exception("[ENTRY] TREND acceptance failed")
        
        # PRIORITY 2: RECLAIM (convexity enhancement, any time)
        if not signal and regime.regime == "RECLAIM":
            try:
                if self.entry_engine.acceptance_ok(snap, state):
                    signal = self.entry_engine.build_signal(regime, snap)
            except Exception:
                self.logger.exception("[ENTRY] RECLAIM acceptance failed")
      
        if not signal:
            return
        
        # Log entry snapshot for audit
        self.logger.log_event("entry_snapshot", {
            "symbol": symbol,
            "snap": snap,
            "signal": {
                "bias": signal.bias,
                "grade": signal.grade,
                "regime": signal.regime,
                "score": signal.score,
                "trail_mult": signal.trail_mult,
            },
            "acceptance_state": {
                "hold_bars": state["hold_bars"],
                "range_high": state["range_high"],
                "range_low": state["range_low"],
            }
        })
        
        # Strike selection
        strike_result = await self.selector.select(
            symbol=symbol,
            underlying_price=price,
            bias=signal.bias,
            chain=chain_rows,
        )
        
        if not strike_result:
            return
        
        # Position sizing
        cap = self.CONTRACT_CAPS.get(symbol, self.DEFAULT_CAP)
        qty = min(int(1000 * self.RISK_PCT / strike_result["premium"]), cap)
        
        if qty < 1:
            return
        
        # Log entry decision
        self.decision_log.log(
            decision="ENTRY",
            symbol=symbol,
            reason=str(signal.regime),  # Use regime field (e.g., "VWAP_RECLAIM", "FORCED")
            convexity_score=float(signal.score),
            tier=str(signal.grade),
            price=float(price),
        )
        
        # Execute
        await self._execute_entry(symbol, signal, strike_result, qty)

    async def _manage_trade(self, symbol: str, price: float):
        """
        Post-entry trade management.
        Implements convexity-based tier promotions.
        """
        
        if symbol != self.active_symbol:
            return
        
        # --------------------------------------------------
        # DEV: Forced exit for SHADOW test harness
        # --------------------------------------------------
        if (
            FORCE_SHADOW_ENTRY
            and self.execution_phase.name == "SHADOW"
            and self.active_contract == "SPY_FORCED_TEST"
        ):
            # Check if 30 seconds elapsed
            if hasattr(self.trail.state, 'entry_ts'):
                elapsed = time.monotonic() - self.trail.state.entry_ts
                if elapsed >= 30.0:
                    self.logger.log_event("forced_shadow_exit", {"forced": True})
                    await self._execute_exit(symbol, reason="FORCED_SHADOW_EXIT")
                    return
            
            # For forced trades, update trail with synthetic price movement
            # ⚠️ DEV-ONLY: Synthetic price path inflates R-multiple
            # This is acceptable for mechanics testing, NOT for expectancy inference
            if self.active_entry_price:
                # Synthetic price: linear increase from entry to +30% over 30 seconds
                elapsed = time.monotonic() - self.trail.state.entry_ts if hasattr(self.trail.state, 'entry_ts') else 0
                synthetic_mid = self.active_entry_price * (1.0 + 0.3 * (elapsed / 30.0))
                
                if self.trail.state.active:
                    self.trail.update(symbol, synthetic_mid)
                    
                    # Check for trail exit
                    if synthetic_mid <= self.trail.state.trail_level:
                        await self._execute_exit(symbol, reason="trail_stop")
                        return
            
            # Skip normal chain lookup for forced trades
            return
        
        try:
            # Get current option mid price
            chain_rows = self.chain_agg.get_chain(symbol)
            contract_row = next(
                (r for r in chain_rows if r["contract"] == self.active_contract),
                None
            )
            
            if not contract_row:
                return
            
            bid = contract_row.get("bid")
            ask = contract_row.get("ask")
            
            if bid is None or ask is None:
                return
            
            mid_price = (bid + ask) / 2
            
            # Calculate PnL %
            entry_to_current = (mid_price - self.active_entry_price) / self.active_entry_price
            
            # Grade based on PnL
            if entry_to_current >= 0.50:
                grade = "A"
            elif entry_to_current >= 0.25:
                grade = "B"
            elif entry_to_current >= 0:
                grade = "C"
            elif entry_to_current >= -0.25:
                grade = "D"
            else:
                grade = "F"
            
            self.logger.log_event("management_convexity", {
                "symbol": symbol,
                "grade": grade,
                "pnl_pct": round(entry_to_current * 100, 2),
            })
            
            # Tier promotion
            current_tier = self.active_grade or "L0"
            
            if current_tier == "L0" and grade in ["A", "B"]:
                self.active_grade = "L1"
                self.logger.log_event("tier_promotion", {
                    "symbol": symbol,
                    "from": "L0",
                    "to": "L1",
                    "grade": grade,
                })
            
            elif current_tier == "L1" and grade == "A":
                self.active_grade = "L2"
                self.logger.log_event("tier_promotion", {
                    "symbol": symbol,
                    "from": "L1",
                    "to": "L2",
                    "grade": grade,
                })
            
            # Convexity collapse → scratch
            if grade in ["D", "F"] and current_tier == "L0":
                await self._execute_exit(symbol, reason="convexity_collapse")
                return
                
        except Exception as e:
            self.logger.log_event("management_convexity_failed", {
                "symbol": symbol, "error": str(e)
            })

    # ------------------------------------------------------------
    async def _execute_entry(self, symbol: str, signal, strike_result: dict, qty: int):
        """
        Execute entry with bracket order.
        """
        
        entry_price = strike_result["premium"]
        
        # Calculate bracket levels
        take_profit = entry_price * getattr(signal, 'trail_mult', 2.0)
        stop_loss = entry_price * 0.50
        
        # Send to execution engine
        try:
            await self.engine.send_bracket(
                symbol=symbol,
                side=signal.bias,
                qty=qty,
                entry_price=entry_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
                meta={
                    "strike": strike_result["strike"],
                    "contract": strike_result["contract"],
                    "grade": getattr(signal, 'grade', 'L0'),
                },
            )
            
        except RuntimeError as e:
            if "SHADOW" not in str(e):
                raise
            
            # Detect forced trades
            is_forced = strike_result.get("contract") == "SPY_FORCED_TEST"
            
            self.logger.log_event("shadow_execution", {
                "symbol": symbol,
                "action": "entry",
                "contract": strike_result["contract"],
                "entry_price": entry_price,
                "qty": qty,
                "forced": is_forced,
            })
        
        # Set active state
        self.active_symbol = symbol
        self.active_contract = strike_result["contract"]
        self.active_bias = signal.bias
        self.active_entry_price = entry_price
        self.active_qty = qty
        self.active_grade = getattr(signal, 'grade', 'L0')
        self.active_score = getattr(signal, 'score', 0.0)
        
        # Start trail logic
        self.trail.initialize(symbol, entry_price, getattr(signal, 'trail_mult', 2.0))
        
        # Persist oneR for R-multiple calculation (canonical risk)
        self.trail.state.oneR = entry_price * 0.50
        
        # Persist entry timestamp for forced exit timing
        self.trail.state.entry_ts = time.monotonic()
        
        # Set trading phase
        self.trading_phase = TradingPhase.IN_TRADE
        
        self.logger.log_event("entry_executed", {
            "symbol": symbol,
            "contract": strike_result["contract"],
            "qty": qty,
            "entry": entry_price,
        })

    # ------------------------------------------------------------
    async def _execute_exit(self, symbol: str, reason: str):
        """
        Execute exit and log results.
        """
        
        if not self.trail.state.active:
            return
        
        # --------------------------------------------------
        # DEV: Handle forced SHADOW trades
        # --------------------------------------------------
        if (
            FORCE_SHADOW_ENTRY
            and self.execution_phase.name == "SHADOW"
            and self.active_contract == "SPY_FORCED_TEST"
        ):
            # Use synthetic exit price for forced trades
            elapsed = time.monotonic() - self.trail.state.entry_ts if hasattr(self.trail.state, 'entry_ts') else 30.0
            exit_price = self.active_entry_price * (1.0 + 0.3 * (elapsed / 30.0))
            
        else:
            # Normal exit: Get current contract price
            chain_rows = self.chain_agg.get_chain(symbol)
            contract_row = next(
                (r for r in chain_rows if r["contract"] == self.active_contract),
                None
            )
            
            if not contract_row:
                self.logger.log_event("exit_no_price", {"symbol": symbol})
                return
            
            bid = contract_row.get("bid")
            ask = contract_row.get("ask")
            
            if bid is None or ask is None:
                return
            
            exit_price = (bid + ask) / 2
        
        # Calculate PnL
        pnl_per_contract = exit_price - self.active_entry_price
        total_pnl = pnl_per_contract * self.active_qty
        pnl_pct = (pnl_per_contract / self.active_entry_price) * 100
        
        # Get underlying price
        underlying_price = self.last_price.get(symbol, 0.0)
        
        # Canonical EXIT decision log
        self.decision_log.log(
            decision="EXIT",
            symbol=symbol,
            reason=str(reason),
            convexity_score=float(self.active_score or 0.0),
            tier=str(self.active_grade or "L0"),
            price=float(underlying_price),
        )
        
        # Send exit order (phase-gated)
        if self.execution_phase.name != "LIVE":
            is_forced = self.active_contract == "SPY_FORCED_TEST"
            self.logger.log_event("shadow_execution", {
                "symbol": symbol,
                "action": "exit",
                "reason": reason,
                "pnl": round(total_pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "forced": is_forced,
            })
        else:
            await self.engine.close_position(
                symbol=symbol,
                contract=self.active_contract,
                qty=self.active_qty,
                exit_price=exit_price,
            )
        
        # Log exit
        self.logger.log_event("exit_executed", {
            "symbol": symbol,
            "reason": reason,
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
        
        # Store last trade view for POST_TRADE display
        oneR = getattr(self.trail.state, 'oneR', self.active_entry_price * 0.50)
        r_multiple = ((exit_price - self.active_entry_price) / oneR) if oneR else 0.0
        
        self.last_trade_view = {
            "symbol": self.active_symbol,
            "entry": self.active_entry_price,
            "exit": exit_price,
            "qty": self.active_qty,
            "pnl_pct": pnl_pct,
            "r_multiple": r_multiple,
            "reason": reason,
            "ts": time.monotonic(),
        }
        
        # Set POST_TRADE phase
        self.trading_phase = TradingPhase.POST_TRADE
        self._post_trade_ts = time.monotonic()
        
        # Set exit timestamp for micro cooldown
        self.last_exit_ts = time.monotonic()
        
        # Clear active state
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None
        self.active_grade = None
        self.active_score = None
        
        # Reset trail state (preserve object, reset state)
        self.trail.state.active = False

    # ------------------------------------------------------------
    async def shutdown(self):
        """Coordinated shutdown."""
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

        print("\033[?25h")
        print("[SYS] Shutdown complete.")