"""
0DTE Options Trading Orchestrator v2.0 (REFACTORED)

REFACTOR SUMMARY:
    - SessionMandate is now the SINGLE AUTHORITY for entry permission
    - All regime detection moved to SessionMandateEngine
    - VWAPTrackerManager provides proper VWAP calculations
    - Entry engines are pure executors (no permission decisions)
    - Enhanced logging for debugging

CONTROL FLOW:
    1. _on_underlying() → Update VWAP + price
    2. _evaluate() → mandate_engine.determine()
    3. if not mandate.allows_entry(): return (HARD STOP)
    4. strike_selector.select() (pure executor)
    5. entry_engine.build_signal() (pure builder)
    6. _execute_entry()

FIXES:
    - VWAP properly calculated via VWAPTrackerManager
    - vwap_dev and vwap_dev_change populated in snap
    - Detailed strike selection failure logging
    - Enhanced observability throughout
"""

import os
import time
import asyncio
import signal
import contextlib
import logging
from typing import Dict, Any, List, Optional

from datetime import datetime, time as dttime
import pytz

# Session Mandate (SINGLE AUTHORITY)
from bot_0dte.strategy.session_mandate import SessionMandateEngine, SessionMandate, RegimeState

# Strategy (pure executors)
from bot_0dte.strategy.elite_entry import EliteEntryEngine, EliteSignal
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.validation.option_trend_validator import OptionTrendValidator

# VWAP Tracker (REFACTORED)
from bot_0dte.indicators.vwap_tracker import VWAPTrackerManager, VWAPResult

# Risk
from bot_0dte.risk.trail_logic import TrailLogic
from bot_0dte.risk.risk_engine import RiskEngine

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


logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main orchestrator for 0DTE options trading.
    
    Coordinates:
        - Market data ingestion (mux)
        - VWAP tracking (VWAPTrackerManager)
        - Entry permission (SessionMandateEngine)
        - Strike selection (StrikeSelector)
        - Signal building (EliteEntryEngine)
        - Risk management (RiskEngine)
        - Order execution (engine)
        - UI rendering (ascii_renderer)
    """

    def __init__(
        self,
        engine,
        mux,
        telemetry: Telemetry,
        logger: StructuredLogger,
        config,  # ✅ Add this
        universe=None,
        auto_trade_enabled=False,
        execution_phase: ExecutionPhase = None,
    ):

        # Phase resolution
        if execution_phase is None:
            execution_phase = ExecutionPhase.from_env(default="paper")
        
        self.execution_phase = execution_phase

        print("\n" + "=" * 70)
        print(f" EXECUTION PHASE: {self.execution_phase.value.upper()} ".center(70, "="))
        print("=" * 70 + "\n")

        # Core
        self.engine = engine
        self.mux = mux
        self.logger = logger
        self.telemetry = telemetry
        self.auto = auto_trade_enabled

        # Market session timing
        self._market_open_ts = None
        
        # Decision + Convexity Loggers
        self.decision_log = DecisionLogger(self.execution_phase.value)
        self.convexity_log = ConvexityLogger(self.execution_phase.value)

        # Universe
        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        # Underlying tracking
        self.last_price: Dict[str, Optional[float]] = {s: None for s in self.symbols}
        
        # ================================================================
        # VWAP TRACKING (REFACTORED - uses VWAPTrackerManager)
        # ================================================================
        self.vwap_manager = VWAPTrackerManager(
            window_size=0,  # Session VWAP
            log_func=lambda msg: None,  # Suppress verbose logging
        )
        
        # Legacy compatibility - expose vwap dict
        self.vwap: Dict[str, Optional[float]] = {}

        # Chain aggregation + freshness
        self.chain_agg = ChainAggregator(self.symbols)
        self.freshness = None

        self.option_trend_validator = OptionTrendValidator(self.chain_agg)

        # Massive snapshot + Greeks
        self.snapshot_client = MassiveSnapshotClient(
            api_key=os.getenv("MASSIVE_API_KEY")
        )

        # Risk engine
        self.risk_engine = RiskEngine(
            account_state=self.engine.account_state,
            config=config,
            decision_logger=self.decision_log,
        )

        # ================================================================
        # SESSION MANDATE ENGINE (SINGLE AUTHORITY)
        # ================================================================
        self.mandate_engine = SessionMandateEngine()
        
        # Strategy engines (pure executors, no permission decisions)
        self.entry_engine = EliteEntryEngine()
        self.selector = StrikeSelector(
            log_func=lambda msg: self.logger.log_event("strike_debug", {"msg": msg}),
        )
        self.trail = TrailLogic(max_loss_pct=0.50)

        # Active trade state
        self.active_symbol: Optional[str] = None
        self.active_contract: Optional[str] = None
        self.active_bias: Optional[str] = None
        self.active_entry_price: Optional[float] = None
        self.active_qty: Optional[int] = None
        self.active_grade: Optional[str] = None
        self.active_score: Optional[float] = None
        
        # Session tracking
        self.session_open_price: Dict[str, float] = {}
        self._last_strike_attempt_ts: Dict[str, float] = {}
        
        # Trading phase (PRE/IN/POST)
        self.trading_phase = TradingPhase.PRE_TRADE
        self._post_trade_ts: Optional[float] = None
        self.last_trade_view: Optional[Dict[str, Any]] = None
        
        # Hydration state
        self.hydration_complete = False

        # ASCII UI components
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
        print(" ASCII UI ORCHESTRATOR v2.0 (REFACTORED) ".center(70, "="))
        print("=" * 70 + "\n")

    def track(self, task: asyncio.Task):
        self._tasks.append(task)
        return task

    def _resolve_market_open_ts(self) -> float:
        """Resolve market open timestamp in monotonic time."""
        try:
            tz = pytz.timezone("US/Eastern")
            now = datetime.now(tz)
            open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
            now_mono = time.monotonic()
            if now < open_dt:
                return now_mono
            seconds_since_open = (now - open_dt).total_seconds()
            return now_mono - seconds_since_open
        except Exception:
            return time.monotonic()

    @property
    def trade_view(self) -> Optional[Dict[str, Any]]:
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

    async def run_test_entry_now(self):
        symbol = "TSLA"
        price = self.last_price.get(symbol)
        if price is None:
            print(f"[TEST ENTRY] No price for {symbol}")
            return

        chain_rows = self.chain_agg.get_chain(symbol)
        if not chain_rows:
            print(f"[TEST ENTRY] No chain for {symbol}")
            return

        reference_price = self.session_open_price.get(symbol)

        snap = {
            "symbol": symbol,
            "price": price,
            "vwap": self.vwap.get(symbol),
            "vwap_dev": None,
            "vwap_dev_change": None,
            "reference_price": reference_price,
            "seconds_since_open": self.seconds_since_open,
        }

        mandate = self.mandate_engine.determine(symbol, snap)
        self.logger.log_event("force_entry_test_started", mandate.to_dict())

        await self._attempt_entry(symbol, mandate, price, chain_rows)


    async def _on_underlying(self, event):
        """Handle underlying price tick with VWAP update."""
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

        # ================================================================
        # VWAP ACCUMULATION (REFACTORED)
        # ================================================================
        vol = event.get("volume", 1.0)
        vwap_result = self.vwap_manager.update(sym, price, vol)
        
        # Update legacy vwap dict
        if vwap_result.is_valid:
            self.vwap[sym] = vwap_result.vwap
        elif sym not in self.vwap:
            self.vwap[sym] = price

        # POST_TRADE fade-back
        if self.trading_phase == TradingPhase.POST_TRADE:
            if self._post_trade_ts and time.monotonic() - self._post_trade_ts > 4.0:
                self.trading_phase = TradingPhase.PRE_TRADE
                self.last_trade_view = None
        
        # UI refresh (throttled)
        if self.ui_clock.ready():
            snap = build_ui_snapshot(self)
            render(snap)

        await self._evaluate(sym, price, vwap_result)

    async def _on_option(self, event):
        """Handle option NBBO tick."""
        sym = event.get("symbol")
        if sym not in self.symbols:
            return

        self.chain_agg.update_from_nbbo(event)

        if self.active_contract and event.get("contract") == self.active_contract:
            bid = event.get("bid")
            ask = event.get("ask")
            
            if bid is not None and ask is not None:
                mid_price = (bid + ask) / 2
                
                if self.trail.state.active:
                    self.trail.update(sym, mid_price)
                    if mid_price <= self.trail.state.trail_level:
                        await self._execute_exit(sym, reason="trail_stop")

    async def _evaluate(self, symbol: str, price: float, vwap_result: VWAPResult):
        """
        Main evaluation loop with VWAP integration.
        REFACTORED: snap now includes vwap_dev and vwap_dev_change from tracker.
        """
        if not self.hydration_complete:
            return

        if self.active_symbol is not None:
            await self._manage_trade(symbol, price)
            return

        if not self.auto:
            return

        chain_rows = self.chain_agg.get_chain(symbol)
        if not chain_rows:
            return

        if symbol not in self.session_open_price and self.seconds_since_open > 0:
            self.session_open_price[symbol] = price

        reference_price = self.session_open_price.get(symbol)

        # Build snap with VWAP data
        snap = {
            "symbol": symbol,
            "price": price,
            "vwap": vwap_result.vwap if vwap_result.is_valid else None,
            "vwap_dev": vwap_result.vwap_dev if vwap_result.is_valid else None,
            "vwap_dev_change": vwap_result.vwap_dev_change if vwap_result.is_valid else None,
            "reference_price": reference_price,
            "seconds_since_open": self.seconds_since_open,
        }

        # ================================================================
        # SESSION MANDATE (SINGLE AUTHORITY)
        # ================================================================
        mandate = self.mandate_engine.determine(symbol, snap)
        self.logger.log_event("session_mandate", mandate.to_dict())

        if not mandate.allows_entry():
            if mandate.state == RegimeState.SUPPRESSED:
                self.logger.log_event("entry_suppressed", {
                    "symbol": symbol,
                    "bias": mandate.bias,
                    "reason": mandate.reason,
                    "confidence": mandate.confidence,
                })
            return

        # ================================================================
        # STRIKE SELECTION (REFACTORED)
        # ================================================================
        now = time.monotonic()
        last_attempt = self._last_strike_attempt_ts.get(symbol, 0)
        if now - last_attempt < 3.0:
            return

        strike_result = self.selector.select(
            symbol=symbol,
            direction=mandate.bias,
            chain_rows=chain_rows,
            underlying_price=price,
        )
        self._last_strike_attempt_ts[symbol] = now

        # ✅ ENHANCED: Log all strike selection failures with full details
        if not strike_result.success:
            self.logger.log_event("strike_selection_failed", {
                "symbol": symbol,
                "bias": mandate.bias,
                "reason": strike_result.failure_reason,
                "details": strike_result.failure_details,
            })
            
            # ✅ NEW: Additional strike_blocked event with flattened details
            self.logger.log_event("strike_blocked", {
                "symbol": symbol,
                "reason": strike_result.failure_reason,
                **strike_result.failure_details,
            })
            return

        strike_dict = strike_result.as_legacy_dict()

        # Log successful selection
        self.logger.log_event("strike_selected", {
            "symbol": symbol,
            "contract": strike_dict.get("contract"),
            "strike": strike_dict.get("strike"),
            "premium": strike_dict.get("premium"),
            "bias": mandate.bias,
        })

        # ================================================================
        # OPTION TREND VALIDATION
        # ================================================================
        try:
            option_trend = await self.option_trend_validator.observe(
                symbol=symbol,
                bias=mandate.bias,
                contract=strike_dict["contract"],
                chain=chain_rows,
                ts=time.monotonic(),
            )
            self.logger.log_event("option_trend_validation", option_trend)
        except Exception as e:
            self.logger.log_event("option_trend_validation_failed", {
                "symbol": symbol, "error": str(e)
            })

        # ================================================================
        # SIGNAL CONSTRUCTION
        # ================================================================
        signal = self.entry_engine.build_signal(mandate, snap)
        self.logger.log_event("entry_snapshot", {
            "symbol": symbol,
            "mandate": mandate.to_dict(),
            "signal": {
                "bias": signal.bias,
                "grade": signal.grade,
                "regime": signal.regime,
                "score": signal.score,
            },
        })

        # ================================================================
        # RISK GATE
        # ================================================================
        trade_intent = {
            "symbol": symbol,
            "signal": signal,
            "strike": strike_dict,
            "option_price": strike_dict["premium"],
            "underlying_price": price,
        }

        # --- RISK GATE (1st occurrence) ---
        approved_intent = await self.risk_engine.approve(trade_intent)
        if approved_intent is None:
            return

        # ================================================================
        # EXECUTION
        # ================================================================
        await self._execute_entry(
            symbol=symbol,
            signal=signal,
            strike_result=strike_dict,
            qty=approved_intent["contracts"],
            price=price,
        )
    async def _attempt_entry(self, symbol, mandate, price, chain_rows):
        """
        Isolated helper for testing entry logic:
        - selects a strike
        - validates signal
        - risk-checks the trade
        - calls _execute_entry() if everything passes
        """
        strike_result = self.selector.select(
            symbol=symbol,
            direction=mandate.bias,
            chain_rows=chain_rows,
            underlying_price=price,
        )

        if not strike_result.success:
            print(f"[ENTRY FAIL] No strike selected for {symbol} - {strike_result}")
            return False

        strike_dict = strike_result.as_legacy_dict()

        signal = self.entry_engine.build_signal(
            mandate, {"symbol": symbol, "price": price}
        )

        trade_intent = {
            "symbol": symbol,
            "signal": signal,
            "strike": strike_dict,
            "option_price": strike_dict["premium"],
            "underlying_price": price,
        }

        # ------------------------------------------------
        # RISK GATE
        # ------------------------------------------------
        approved_intent = await self.risk_engine.approve(trade_intent)
        print(f"[DEBUG] approved_intent = {approved_intent}")
        if approved_intent is None:
            print(f"[ENTRY BLOCKED] Risk rejected trade for {symbol}")
            return False

        print(
            f"[ENTRY PASS] {symbol} "
            f"contracts={approved_intent['contracts']} "
            f"premium={strike_dict['premium']}"
        )

        await self._execute_entry(
            symbol=symbol,
            signal=signal,
            strike_result=strike_dict,
            qty=approved_intent["contracts"],
            price=price,
        )

        return True

    async def _manage_trade(self, symbol: str, price: float):
        """Post-entry trade management with tier promotions."""
        if symbol != self.active_symbol:
            return
        
        try:
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
            entry_to_current = (mid_price - self.active_entry_price) / self.active_entry_price
            
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
            
            current_tier = self.active_grade or "L0"
            
            if current_tier == "L0" and grade in ["A", "B"]:
                self.active_grade = "L1"
                self.logger.log_event("tier_promotion", {
                    "symbol": symbol, "from": "L0", "to": "L1", "grade": grade,
                })
            elif current_tier == "L1" and grade == "A":
                self.active_grade = "L2"
                self.logger.log_event("tier_promotion", {
                    "symbol": symbol, "from": "L1", "to": "L2", "grade": grade,
                })
            
            if grade in ["D", "F"] and current_tier == "L0":
                await self._execute_exit(symbol, reason="convexity_collapse")
                
        except Exception as e:
            self.logger.log_event("management_convexity_failed", {
                "symbol": symbol, "error": str(e)
            })

    async def _execute_entry(self, symbol: str, signal, strike_result: dict, qty: int, price: float):
        """Execute entry with bracket order."""
        entry_price = strike_result["premium"]
        
        self.active_symbol = symbol
        self.active_contract = strike_result["contract"]
        self.active_bias = signal.bias
        self.active_entry_price = entry_price
        self.active_qty = qty
        self.active_grade = getattr(signal, 'grade', 'L0')
        self.active_score = getattr(signal, 'score', 0.0)
        
        self.logger.log_event("execution_attempt", {
            "symbol": symbol,
            "contract": strike_result["contract"],
            "qty": qty,
            "entry_price": entry_price,
            "phase": self.execution_phase.value,
        })
        
        execution_success = False
        take_profit = entry_price * getattr(signal, 'trail_mult', 2.0)
        stop_loss = entry_price * 0.50
        
        try:
            result = await self.engine.send_bracket(
                symbol=symbol,
                side=signal.bias,
                qty=qty,
                entry_price=entry_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
                meta={
                    "strike": strike_result["strike"],
                    "contract": strike_result["contract"],
                    "grade": getattr(signal, "grade", "L0"),
                },
            )

            if result:
                status = result.get("status", "").lower()
                if status in ["filled", "mock-filled", "submitted", "pending"]:
                    execution_success = True
                    self.logger.log_event("order_sent", {
                        "symbol": symbol,
                        "status": status,
                        "phase": self.execution_phase.value,
                    })
                else:
                    print(f"[ORDER NOT FILLED] Status={status} → result={result}")
                    self.logger.log_event("order_blocked", {
                        "symbol": symbol,
                        "status": status,
                        "details": result,
                    })
            else:
                print(f"[ORDER FAILED] No result returned from engine.send_bracket()")
                self.logger.log_event("order_blocked", {
                    "symbol": symbol,
                    "status": "none",
                })

        except RuntimeError as e:
            if "SHADOW" in str(e):
                self.logger.log_event("shadow_execution", {
                    "symbol": symbol,
                    "action": "entry",
                    "contract": strike_result["contract"],
                    "entry_price": entry_price,
                    "qty": qty,
                })
                execution_success = True
            else:
                self.logger.log_event("order_failed", {"symbol": symbol, "error": str(e)})

        except Exception as e:
            self.logger.log_event("order_failed", {"symbol": symbol, "error": str(e)})
        
        if execution_success:
            self.decision_log.log(
                decision="ENTRY",
                symbol=symbol,
                reason=str(signal.regime),
                convexity_score=float(signal.score),
                tier=str(signal.grade),
                price=float(price),
            )
            
            self.trail.initialize(symbol, entry_price, getattr(signal, 'trail_mult', 2.0))
            self.trail.state.oneR = entry_price * 0.50
            self.trail.state.entry_ts = time.monotonic()
            self.trading_phase = TradingPhase.IN_TRADE
            
            self.logger.log_event("entry_executed", {
                "symbol": symbol,
                "contract": strike_result["contract"],
                "qty": qty,
                "entry": entry_price,
            })
        else:
            self._rollback_entry_state()

    def _rollback_entry_state(self):
        """Rollback active trade state on execution failure."""
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None
        self.active_grade = None
        self.active_score = None
        self.trail.state.active = False
        self.trading_phase = TradingPhase.PRE_TRADE

    async def _execute_exit(self, symbol: str, reason: str):
        """Execute exit and log results."""
        if not self.trail.state.active:
            return
        
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
        pnl_per_contract = exit_price - self.active_entry_price
        total_pnl = pnl_per_contract * self.active_qty
        pnl_pct = (pnl_per_contract / self.active_entry_price) * 100
        underlying_price = self.last_price.get(symbol, 0.0)
        
        self.decision_log.log(
            decision="EXIT",
            symbol=symbol,
            reason=str(reason),
            convexity_score=float(self.active_score or 0.0),
            tier=str(self.active_grade or "L0"),
            price=float(underlying_price),
        )
        
        if self.execution_phase.name != "LIVE":
            self.logger.log_event("shadow_execution", {
                "symbol": symbol,
                "action": "exit",
                "reason": reason,
                "pnl": round(total_pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            })
        else:
            await self.engine.close_position(
                symbol=symbol,
                contract=self.active_contract,
                qty=self.active_qty,
                exit_price=exit_price,
            )
        
        self.logger.log_event("exit_executed", {
            "symbol": symbol,
            "reason": reason,
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
        
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
            "entry_ts": getattr(self.trail.state, 'entry_ts', time.monotonic()),
        }
        
        self.trading_phase = TradingPhase.POST_TRADE
        self._post_trade_ts = time.monotonic()
        self.mandate_engine.set_last_exit_ts(time.monotonic())
        
        self._rollback_entry_state()

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

        print("\033[?25h")
        print("[SYS] Shutdown complete.")