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
from bot_0dte.ui.live_panel import LivePanel
from bot_0dte.ui.ui_state import UIState, TradeState
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
        self.active_grade = None  # Store tier for EXIT logging
        self.active_score = None  # Store convexity_score for EXIT logging
        
        # -------------------------------------------------
        # Hydration state
        # -------------------------------------------------
        self.hydration_complete = False

        # -------------------------------------------------
        # Dashboard (Simple ASCII panel)
        # -------------------------------------------------
        self.ui_state = UIState()
        self.panel = LivePanel()
        self.panel.attach_ui_state(self.ui_state)

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
        self._shutdown = None  # Will be created in start()
        self._tasks: list[asyncio.Task] = []
        self._shutdown_created = False

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
            self._shutdown_created = True
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
        
        # Skip blocking REST hydration at startup - too slow
        # Instead, launch background task to fetch Greeks gradually
        print("[WARMUP] Skipping startup hydration - launching background Greek fetcher")
        
        # Start background Greek fetcher
        greek_task = asyncio.create_task(self._fetch_greeks_background())
        self.track(greek_task)
        
        print(f"[OK] fetch_snapshot_and_hydrate() in {time.monotonic() - t0:.2f}s")
        
        print("[START] Marking hydration complete")
        self.hydration_complete = True
        print("[OK] Hydration complete flag set - background Greek fetcher running")
        
        # Assign freshness tracker
        print("[START] Assigning freshness")
        self.freshness = self.mux.freshness
        print("[OK] Freshness assigned")
        
        # LivePanel is ready (renders on update() calls from callbacks)
        print("[INFO] LivePanel initialized - will render on price updates")
        
        print("[ORCHESTRATOR] start() completed successfully")
    
    # ------------------------------------------------------------
    async def _fetch_greeks_background(self):
        """Background task to gradually fetch Greeks via REST and merge into chain."""
        print("[GREEKS] Background Greek fetcher started")
        
        # Wait 5 seconds for NBBO to populate first
        await asyncio.sleep(5)
        
        if not hasattr(self.mux, 'engines'):
            return
        
        snap_client = self.mux.parent_orchestrator.snapshot_client if self.mux.parent_orchestrator else None
        if not snap_client:
            print("[GREEKS] No snapshot client available")
            return
        
        for sym, eng in self.mux.engines.items():
            occ_list = eng.current_subs.get(sym, [])
            print(f"[GREEKS] Fetching Greeks for {sym}: {len(occ_list)} contracts")
            
            for occ in occ_list:
                try:
                    # Fetch with timeout
                    rest = await asyncio.wait_for(
                        snap_client.fetch_contract(sym, occ),
                        timeout=10.0
                    )
                    
                    if rest:
                        # Merge Greeks into existing chain row
                        self.chain_agg.update_from_snapshot(sym, occ, rest)
                        print(f"[GREEKS] ✓ {occ}")
                    
                    # Small delay between requests to avoid rate limiting
                    await asyncio.sleep(0.5)
                    
                except asyncio.TimeoutError:
                    print(f"[GREEKS] Timeout {occ}")
                except Exception as e:
                    print(f"[GREEKS] Error {occ}: {e}")
                    
        print(f"[GREEKS] Background fetcher complete for all symbols")

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
        
        # Update LivePanel (throttled internally to 0.25s)
        self.panel.update(
            symbol=sym,
            price=price,
            bid=None,  # Will add later
            ask=None,
            signal=None,
            strike=None,
            expiry=None
        )

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
        """Main strategy evaluation on each underlying tick."""       

        # Initialize decision tracking
        decision = "HOLD"
        reason = "no_signal"
        convexity_score = 0.0
        tier = "L0"
        
        # Skip if hydration not complete
        if not self.hydration_complete:
            decision = "BLOCK"
            reason = "hydration_incomplete"
        
        # Skip if already in position
        elif self.trail.state.active:
            decision = "BLOCK"
            reason = "position_active"
        
        else:
            # Build snapshot for entry engine
            vwap_data = self.vwap[symbol].update(price)
            
            # Log the evaluation
            self.logger.log_event("evaluation_tick", {
                "symbol": symbol,
                "price": price,
                "vwap": round(vwap_data["vwap"], 2),
                "vwap_dev": round(vwap_data["vwap_dev"], 4),
                "vwap_dev_change": round(vwap_data["vwap_dev_change"], 4),
            })
            
            # Get chain data for Greeks
            chain_rows = self.chain_agg.get_chain(symbol)
            
            # DEBUG: Check what's in the cache
            cache_size = len(self.chain_agg.cache.get(symbol, {}))
            self.logger.log_event("evaluation_chain_debug", {
                "symbol": symbol,
                "chain_rows_returned": len(chain_rows),
                "cache_contracts": cache_size,
                "cache_keys": list(self.chain_agg.cache.get(symbol, {}).keys())[:3] if cache_size > 0 else []
            })
            
            if not chain_rows:
                self.logger.log_event("evaluation_no_chain", {"symbol": symbol})
                # decision remains "HOLD", reason remains "no_signal"
            
            else:
                # Calculate aggregated Greeks (handle None from NBBO-only rows)
                atm_calls = [r for r in chain_rows if r["right"] == "C" and r.get("delta") is not None]
                atm_puts = [r for r in chain_rows if r["right"] == "P" and r.get("delta") is not None]
                
                avg_call_delta = sum(r["delta"] for r in atm_calls) / len(atm_calls) if atm_calls else None
                avg_put_delta = sum(r["delta"] for r in atm_puts) / len(atm_puts) if atm_puts else None
                
                # Filter for rows with gamma before calculating average
                gamma_rows = [r for r in chain_rows if r.get("gamma") is not None]
                avg_gamma = sum(r["gamma"] for r in gamma_rows) / len(gamma_rows) if gamma_rows else None
                
                # Build snapshot
                now_et = datetime.now(self._tz)
                seconds_since_open = int((now_et - self._session_open_dt).total_seconds())
                
                snap = {
                    "symbol": symbol,
                    "price": price,
                    "vwap": vwap_data["vwap"],
                    "vwap_dev": vwap_data["vwap_dev"],
                    "vwap_dev_change": vwap_data["vwap_dev_change"],
                    "seconds_since_open": seconds_since_open,
                    "delta": avg_call_delta if avg_call_delta else avg_put_delta,
                    "gamma": avg_gamma,
                    "iv": None,  # Would need to calculate from chain
                    "iv_change": None,
                }
                
                # Log the snapshot before qualification
                self.logger.log_event("evaluation_snapshot", {
                    "symbol": symbol,
                    "price": price,
                    "vwap_dev": round(vwap_data["vwap_dev"], 4),
                    "slope": round(vwap_data["vwap_dev_change"], 4),
                    "seconds_since_open": seconds_since_open,
                    "avg_gamma": round(avg_gamma, 4) if avg_gamma else None,
                    "call_delta": round(avg_call_delta, 4) if avg_call_delta else None,
                    "put_delta": round(avg_put_delta, 4) if avg_put_delta else None,
                })
                
                # Check for entry signal
                signal = self.entry_engine.qualify(snap)
                if not signal:
                    self.logger.log_event("evaluation_no_signal", {
                        "symbol": symbol,
                        "reason": "entry_engine_rejected"
                    })
                    # decision remains "HOLD", reason remains "no_signal"
                
                else:
                    # Extract convexity metrics from signal
                    convexity_score = signal.score
                    tier = signal.grade
                    
                    # Log the signal
                    self.logger.log_event("elite_signal", {
                        "symbol": symbol,
                        "bias": signal.bias,
                        "grade": signal.grade,
                        "regime": signal.regime,
                        "score": signal.score,
                    })
                    
                    # Select strike
                    strike_result = await self.selector.select_from_chain(
                        chain_rows=chain_rows,
                        bias=signal.bias,
                        underlying_price=price
                    )
                    
                    if not strike_result:
                        self.logger.log_event("strike_selection_failed", {"symbol": symbol})
                        decision = "BLOCK"
                        reason = "strike_selection_failed"
                    
                    else:
                        # Calculate position size
                        cap = self.CONTRACT_CAPS.get(symbol, self.DEFAULT_CAP)
                        qty = min(cap, max(1, int(10000 * self.RISK_PCT / strike_result["premium"])))
                        
                        # Set ENTER decision
                        decision = "ENTER"
                        reason = f"{signal.bias.lower()}_entry"
                        
                        # Execute entry
                        await self._execute_entry(symbol, signal, strike_result, qty)
        
        # Canonical decision log (exactly once per evaluation)
        self.decision_log.log(
            decision=decision,
            symbol=symbol,
            reason=reason,
            convexity_score=convexity_score,
            tier=tier,
            price=price
        )
    
    # ------------------------------------------------------------
    async def _execute_entry(self, symbol: str, signal, strike_result: dict, qty: int):
        """Execute entry with bracket order."""
        
        entry_price = strike_result["premium"]
        
        # Calculate bracket levels
        take_profit = entry_price * signal.trail_mult
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
                    "grade": signal.grade,
                },
            )
            
        except RuntimeError as e:
            # SHADOW mode raises RuntimeError - this is expected and correct
            if "SHADOW" not in str(e):
                raise
            
            # Log shadow execution
            self.logger.log_event("shadow_execution", {
                "symbol": symbol,
                "action": "entry",
                "contract": strike_result["contract"],
                "entry_price": entry_price,
                "qty": qty,
            })
        
        # CRITICAL: Set active state AFTER execution attempt (works in all modes)
        # This allows SHADOW mode to track positions and run trail logic
        self.active_symbol = symbol
        self.active_contract = strike_result["contract"]
        self.active_bias = signal.bias
        self.active_entry_price = entry_price
        self.active_qty = qty
        self.active_grade = signal.grade  # Store for EXIT logging
        self.active_score = signal.score  # Store for EXIT logging
        
        # Start trail logic
        self.trail.start(symbol, entry_price)
        
        self.logger.log_event("entry_executed", {
            "symbol": symbol,
            "contract": strike_result["contract"],
            "qty": qty,
            "entry": entry_price,
        })

    # ------------------------------------------------------------
    async def _execute_exit(self, symbol: str, reason: str):
        """Execute exit and log results."""
        
        if not self.trail.state.active:
            return
        
        # Get current contract price from chain
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
        
        # Get underlying price at exit time
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
        
        # Send exit order
        try:
            await self.engine.close_position(
                symbol=symbol,
                contract=self.active_contract,
                qty=self.active_qty,
                exit_price=exit_price,
            )
        except RuntimeError as e:
            if "SHADOW" not in str(e):
                raise
            
            # Log shadow exit
            self.logger.log_event("shadow_execution", {
                "symbol": symbol,
                "action": "exit",
                "reason": reason,
                "pnl": round(total_pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            })
        
        # Log exit execution
        self.logger.log_event("exit_executed", {
            "symbol": symbol,
            "reason": reason,
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
        
        # Clear active state AFTER logging
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None
        self.active_grade = None
        self.active_score = None
        
        # Stop trail
        self.trail.stop()

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

        # Restore cursor (LivePanel doesn't hide it)
        print("\033[?25h")

        print("[SYS] Shutdown complete.")