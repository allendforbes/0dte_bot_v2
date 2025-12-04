"""
Elite Orchestrator — A2-M Edition (WS-Native Micro-Breakout Engine)
-------------------------------------------------------------------

Signal engine:     → VWAP-only (no greeks as predictors)
Chain enrichment:  → greeks, IV, microstructure
Strike selection:  → convexity (gamma), delta-target, premium ceilings
Latency precheck:  → greeks + IV + liquidity + spread + A2-M extensions
Trail logic:       → unchanged
Execution:         → unchanged

A2-M Philosophy:
    greeks = filters (quality), NOT predictors (direction)
"""

import asyncio
import datetime as dt
import time
from typing import Dict, Any, List, Optional

# Strategy layers
from bot_0dte.strategy.elite_entry_diagnostic import EliteEntryEngine, EliteSignal
from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.strategy.elite_latency_precheck import EliteLatencyPrecheck

# Risk
from bot_0dte.risk.trail_logic import TrailLogic

# UI
from bot_0dte.ui.live_panel import LivePanel
from bot_0dte.ui.ui_state import UIState

# Infra
from bot_0dte.universe import (
    get_universe_for_today,
    get_expiry_for_symbol,
    max_latency_ms,
    max_premium,
)
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry

# Sizing
from bot_0dte.sizing import size_from_equity


# ============================================================================
# VWAP Tracker
# ============================================================================
class VWAPTracker:
    """Rolling VWAP used by the EliteEntry engine."""

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.prices = []
        self.volumes = []
        self.last_vwap = None
        self.last_vwap_dev = None
        self.last_dev = 0.0

    def update(self, price: float, volume: float = 1.0):
        self.prices.append(price)
        self.volumes.append(volume)

        if len(self.prices) > self.window_size:
            self.prices.pop(0)
            self.volumes.pop(0)

        total_pv = sum(p * v for p, v in zip(self.prices, self.volumes))
        total_v = sum(self.volumes)

        vwap = total_pv / total_v if total_v else price
        dev = price - vwap
        change = dev - self.last_dev
        self.last_dev = dev

        self.last_vwap = vwap
        self.last_vwap_dev = dev

        return {
            "vwap": vwap,
            "vwap_dev": dev,
            "vwap_dev_change": change,
        }


# ============================================================================
# Microstructure Helpers (unchanged)
# ============================================================================
def compute_upvol_pct(chain_rows):
    if not chain_rows:
        return None
    call_vol = sum(r.get("volume") or 0 for r in chain_rows if r["right"] == "C")
    put_vol = sum(r.get("volume") or 0 for r in chain_rows if r["right"] == "P")
    total = call_vol + put_vol
    if total == 0:
        return None
    return 100 * call_vol / total


def compute_flow_ratio(chain_rows):
    calls = [r["premium"] for r in chain_rows if r["right"] == "C" and r["premium"]]
    puts = [r["premium"] for r in chain_rows if r["right"] == "P" and r["premium"]]
    if not calls or not puts:
        return None
    call_avg = sum(calls) / len(calls)
    put_avg = sum(puts) / len(puts)
    if put_avg == 0:
        return None
    return call_avg / put_avg


def compute_iv_change(chain_rows):
    ivs = [r.get("iv") for r in chain_rows if r.get("iv") and r["right"] == "C"]
    if not ivs:
        return None
    if len(ivs) < 2:
        return 0.0
    return ivs[-1] - (sum(ivs) / len(ivs))


def compute_skew_shift(chain_rows):
    calls = [r.get("iv") for r in chain_rows if r["right"] == "C" and r.get("iv")]
    puts = [r.get("iv") for r in chain_rows if r["right"] == "P" and r.get("iv")]
    if not calls or not puts:
        return None
    return (sum(calls) / len(calls)) - (sum(puts) / len(puts))


# ============================================================================
# ORCHESTRATOR — A2-M
# ============================================================================
class Orchestrator:
    """One-trade-at-a-time micro-breakout engine with A2-M enhancements."""

    def __init__(
        self,
        engine,
        mux,
        telemetry: Telemetry,
        logger: StructuredLogger,
        universe=None,
        auto_trade_enabled=False,
        trade_mode="shadow",
    ):
        self.engine = engine
        self.mux = mux
        self.logger = logger
        self.telemetry = telemetry
        self.auto = auto_trade_enabled
        self.trade_mode = trade_mode

        # Symbols + expiries
        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        # Price maps
        self.last_price = {s: None for s in self.symbols}

        # VWAP
        self.vwap = {s: VWAPTracker() for s in self.symbols}

        # Chain aggregator — A2-M ChainSnapshot enabled
        self.chain_agg = ChainAggregator(self.symbols)
        self.chain_ready_ts = {s: 0.0 for s in self.symbols}

        # Warm-up
        self.start_ts = time.time()

        # Strategy stack
        self.entry_engine = EliteEntryEngine()
        self.latency = EliteLatencyPrecheck()
        self.selector = StrikeSelector()
        self.trail = TrailLogic(max_loss_pct=0.50)

        # UI
        self.ui = LivePanel()
        self.ui_state = UIState()
        self.ui.attach_ui_state(self.ui_state)

        # Active trade state
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None

        print("\n" + "=" * 70)
        print(" ELITE ORCHESTRATOR (A2-M) INITIALIZED ".center(70, "="))
        print("=" * 70 + "\n")

    # ----------------------------------------------------------------------
    def notify_chain_refresh(self, symbol: str):
        now = time.time()
        last = self.chain_agg.last_ts.get(symbol, 0)

        if now - last < 0.050:
            return

        self.chain_ready_ts[symbol] = now
        self.chain_agg.last_ts[symbol] = now

        self.logger.log_event(
            "chain_refreshed",
            {"symbol": symbol, "age_ms": round((now - last) * 1000, 2)},
        )

    # ----------------------------------------------------------------------
    async def start(self):
        self.mux.on_underlying(self._on_underlying)
        self.mux.on_option(self._on_option)
        self.mux.parent_orchestrator = self
        await self.mux.connect(self.symbols, self.expiry_map)

    # ----------------------------------------------------------------------
    async def _on_underlying(self, event):
        sym = event.get("symbol")
        price = event.get("price")
        if sym not in self.symbols or price is None:
            return

        self.last_price[sym] = price

        # VWAP/UI Update
        self.ui_state.update_underlying(
            symbol=sym,
            price=price,
            bid=event.get("bid"),
            ask=event.get("ask"),
            signal=self.ui_state.underlying.get(sym, {}).get("signal"),
            strike=self.ui_state.underlying.get(sym, {}).get("strike"),
        )

        self.ui.update(
            symbol=sym,
            price=price,
            bid=event.get("bid"),
            ask=event.get("ask"),
            signal=self.ui_state.underlying[sym].get("signal"),
            strike=self.ui_state.underlying[sym].get("strike"),
        )

        await self._evaluate(sym, price)

    # ----------------------------------------------------------------------
    async def _on_option(self, event):
        sym = event.get("symbol")
        if sym not in self.symbols:
            return

        # A2-M: use update_from_nbbo
        if hasattr(self.chain_agg, "update_from_nbbo"):
            self.chain_agg.update_from_nbbo(event)
        else:
            self.chain_agg.update(event)

        if not self.trail.state.active:
            return

        if event.get("contract") != self.active_contract:
            return

        bid = event.get("bid")
        ask = event.get("ask")
        if bid is None or ask is None:
            return

        mid = (bid + ask) / 2

        # UI PnL update
        if self.ui_state.trade.active:
            self.ui_state.trade.curr_price = mid
            if self.active_entry_price:
                self.ui_state.trade.pnl_pct = (
                    (mid - self.active_entry_price) / self.active_entry_price * 100
                )
            self.ui_state.trade.last_update_ms = 0

        # Hard SL
        if mid <= self.active_entry_price * 0.50:
            self.logger.log_event("hard_stop_triggered", {"mid": mid})
            await self._execute_exit(sym, reason="hard_sl")
            return

        # Trail
        trail_res = self.trail.update(sym, mid)
        if trail_res.get("should_exit"):
            self.logger.log_event("trail_exit_triggered", trail_res)
            await self._execute_exit(sym, reason="trail_exit")

    # ----------------------------------------------------------------------
    async def _evaluate(self, symbol: str, price: float):
        if self.active_symbol:
            return

        vwap_data = self.vwap[symbol].update(price)

        # ChainSnapshot (A2-M)
        chain_snapshot = self.chain_agg.get(symbol)
        if not chain_snapshot:
            return

        chain_rows = chain_snapshot.rows

        # Microstructure snapshot
        snap = {
            "symbol": symbol,
            "price": price,
            "vwap": vwap_data["vwap"],
            "vwap_dev": vwap_data["vwap_dev"],
            "vwap_dev_change": vwap_data["vwap_dev_change"],
            "upvol_pct": compute_upvol_pct(chain_rows),
            "flow_ratio": compute_flow_ratio(chain_rows),
            "iv_change": compute_iv_change(chain_rows),
            "skew_shift": compute_skew_shift(chain_rows),
            "seconds_since_open": self._seconds_since_open(),
        }

        sig: Optional[EliteSignal] = self.entry_engine.qualify(snap)
        if not sig:
            return

        self.logger.log_event("signal_generated", sig.__dict__)
        print(f"[SIGNAL] {symbol} {sig.bias} ({sig.grade}, {sig.regime}) — monitoring…")

        # warm-up freshness handling
        now_ms = time.time() * 1000
        if time.time() - self.start_ts < 8:
            if not chain_snapshot.is_fresh(now_ms, 1000):
                return
        else:
            if not chain_snapshot.is_fresh(now_ms, 750):  # A2-M threshold
                self.logger.log_event("signal_dropped", {"reason": "stale_chain"})
                return

        if not chain_rows:
            self.logger.log_event("signal_dropped", {"reason": "empty_chain"})
            return

        # Strike selection (A2-M: delta/gamma/premium ceiling integrated already)
        strike = await self.selector.select_from_chain(
            chain_rows,
            sig.bias,
            price,
        )
        if not strike:
            self.logger.log_event("signal_dropped", {"reason": "no_strike"})
            return

        print(f"[SIGNAL] {symbol} optimal strike → {strike['strike']}{strike['right']} @ {strike['premium']:.2f}")

        # Premium ceiling guard (A2-M)
        if strike["premium"] > max_premium(symbol):
            self.logger.log_event("entry_blocked", {"reason": "premium_ceiling"})
            return

        # latency pre-check (A2-M includes latency_ms, gamma guards, delta guards)
        tick = {
            "price": strike["premium"],
            "bid": strike["bid"],
            "ask": strike["ask"],
            "vwap_dev_change": vwap_data["vwap_dev_change"],
            "delta": strike.get("delta"),
            "gamma": strike.get("gamma"),
            "_chain_age_ms": now_ms - chain_snapshot.last_update_ts_ms,
            "latency_ms": self.telemetry.latency_ms.get(symbol),  # optional
        }

        pre = self.latency.validate(symbol, tick, sig.bias, sig.grade, snap)
        if not pre.ok:
            self.logger.log_event("entry_blocked", {"reason": pre.reason})
            return

        entry_price = pre.limit_price
        if entry_price is None:
            self.logger.log_event("entry_blocked", {"reason": "no_entry_price"})
            return

        # sizing
        premium = strike["premium"]
        if premium <= 0:
            self.logger.log_event("entry_blocked", {"reason": "invalid_premium"})
            return

        if not self.engine.account_state.is_fresh():
            self.logger.log_event("entry_blocked", {"reason": "stale_equity"})
            return

        qty = size_from_equity(self.engine.account_state.net_liq, premium)

        # Trail activation
        self.trail.initialize(symbol, entry_price, sig.trail_mult)

        # set active state
        self.active_symbol = symbol
        self.active_contract = strike["contract"]
        self.active_bias = sig.bias
        self.active_entry_price = entry_price
        self.active_qty = qty

        # UI activation
        ts = self.ui_state.trade
        ts.active = True
        ts.symbol = symbol
        ts.contract = strike["contract"]
        ts.bias = sig.bias
        ts.regime = sig.regime
        ts.grade = sig.grade
        ts.strike = strike["strike"]
        ts.entry_price = entry_price
        ts.curr_price = entry_price
        ts.pnl_pct = 0.0
        ts.trail_mult = sig.trail_mult
        ts.trail_target = entry_price * (1 + sig.trail_mult)
        ts.hard_sl = entry_price * 0.50

        print(f"[SIGNAL] ENTRY EXECUTING → {symbol} {strike['contract']} @ {entry_price:.2f}")

        if not self.auto:
            self.logger.log_event("shadow_entry", {
                "symbol": symbol,
                "contract": strike["contract"],
                "entry": entry_price,
                "qty": qty,
                "bias": sig.bias,
                "regime": sig.regime,
                "grade": sig.grade,
                "score": sig.score,
            })
            return

        order = await self.engine.send_market(
            symbol=symbol,
            side=sig.bias,
            qty=qty,
            price=entry_price,
            meta={
                "regime": sig.regime,
                "grade": sig.grade,
                "score": sig.score,
            },
        )
        self.logger.log_event("entry_order", order)

    # ----------------------------------------------------------------------
    async def _execute_exit(self, symbol: str, reason: str):
        qty = self.active_qty
        bias = self.active_bias

        if self.trade_mode != "shadow":
            order = await self.engine.send_market(
                symbol=symbol,
                side="SELL" if bias == "CALL" else "BUY",
                qty=qty,
                price=None,
                meta={"reason": reason},
            )
            self.logger.log_event("exit_order", order)
        else:
            self.logger.log_event("shadow_exit", {"reason": reason})

        # clear UI
        ts = self.ui_state.trade
        ts.active = False
        ts.symbol = ts.contract = ts.bias = None
        ts.entry_price = ts.curr_price = ts.pnl_pct = None
        ts.strike = ts.regime = ts.grade = None
        ts.trail_mult = ts.last_update_ms = None

        # reset internal state
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None
        self.trail.state.active = False

        self.ui.set_status(f"{symbol}: exited ({reason})")

    # ----------------------------------------------------------------------
    def _seconds_since_open(self):
        now = dt.datetime.now().astimezone()
        open_t = now.replace(hour=9, minute=30, second=0)
        return max(0.0, (now - open_t).total_seconds())
