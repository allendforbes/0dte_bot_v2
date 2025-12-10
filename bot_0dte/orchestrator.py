"""
Elite Orchestrator — A2-M Edition (FreshnessV2 + Massive NBBO)
--------------------------------------------------------------
Includes:
    • Dynamic VWAP tracking
    • Freshness gating (startup, reconnect, stale frames)
    • StrikeSelector v3.3
    • EntryEngine, LatencyPrecheck
    • NEW: 5% nominal risk-based sizing (live NetLiq)
    • NEW: Underlying category contract caps (20/10/5)
    • Always SELL to exit CALL or PUT
"""

import asyncio
import time  # keep module
from typing import Dict, Any, List

# Time / timezone
from datetime import datetime, date, time as dttime
import pytz

# Strategy components
from bot_0dte.strategy.elite_entry_diagnostic import EliteEntryEngine
from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.strategy.elite_latency_precheck import EliteLatencyPrecheck
from bot_0dte.strategy.continuation_engine import ContinuationEngine

# Risk
from bot_0dte.risk.trail_logic import TrailLogic

# UI
from bot_0dte.ui.live_panel import LivePanel
from bot_0dte.ui.ui_state import UIState

# Infra
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


# ============================================================================
# VWAP TRACKING
# ============================================================================
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

        total_pv = sum(p*v for p,v in zip(self.prices, self.volumes))
        total_v = sum(self.volumes)
        vwap = total_pv / total_v if total_v else price

        dev = price - vwap
        change = dev - self.last_dev
        self.last_dev = dev

        return {"vwap": vwap, "vwap_dev": dev, "vwap_dev_change": change}


# ============================================================================
# MICROSTRUCTURE HELPERS
# ============================================================================
def compute_upvol_pct(rows):
    if not rows:
        return None
    call_vol = sum(r.get("volume") or 0 for r in rows if r["right"] == "C")
    put_vol  = sum(r.get("volume") or 0 for r in rows if r["right"] == "P")
    tot = call_vol + put_vol
    return None if tot == 0 else 100 * call_vol / tot


def compute_flow_ratio(rows):
    calls = [r["premium"] for r in rows if r["right"]=="C" and r["premium"]]
    puts  = [r["premium"] for r in rows if r["right"]=="P" and r["premium"]]
    if not calls or not puts:
        return None
    put_avg = sum(puts)/len(puts)
    return None if put_avg == 0 else (sum(calls)/len(calls)) / put_avg


def compute_iv_change(rows):
    ivs = [r.get("iv") for r in rows if r["right"]=="C" and r.get("iv")]
    if not ivs:
        return None
    return 0.0 if len(ivs) < 2 else ivs[-1] - (sum(ivs)/len(ivs))


def compute_skew_shift(rows):
    calls = [r.get("iv") for r in rows if r["right"]=="C" and r.get("iv")]
    puts  = [r.get("iv") for r in rows if r["right"]=="P" and r.get("iv")]
    if not calls or not puts:
        return None
    return (sum(calls)/len(calls)) - (sum(puts)/len(puts))


# ============================================================================
# ORCHESTRATOR (A2-M)
# ============================================================================
class Orchestrator:
    RISK_PCT = 0.05
    CONTRACT_CAPS = {
        "SPY":20, "QQQ":20,
        "AAPL":10,"AMZN":10,"META":10,
        "MSFT":10,"NVDA":10,"TSLA":10,
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
        trade_mode="shadow",
    ):
        self.engine = engine
        self.mux = mux
        self.logger = logger
        self.telemetry = telemetry
        self.auto = auto_trade_enabled
        self.trade_mode = trade_mode

        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        self.last_price = {s: None for s in self.symbols}
        self.vwap = {s: VWAPTracker() for s in self.symbols}

        self.chain_agg = ChainAggregator(self.symbols)
        self.freshness = None

        self.entry_engine = EliteEntryEngine()
        self.latency = EliteLatencyPrecheck()
        self.selector = StrikeSelector()
        self.trail = TrailLogic(max_loss_pct=0.50)
        self.continuation = ContinuationEngine()

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

        # MARKET CLOCK (Correct)
        self._tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(self._tz)
        self._session_open_dt = self._tz.localize(
            datetime.combine(now_et.date(), dttime(9,30))
        )

        print("\n" + "="*70)
        print(" ELITE ORCHESTRATOR (A2-M / FreshnessV2 / Sizing v5.0) ".center(70,"="))
        print("="*70 + "\n")


    # ----------------------------------------------------------------------
    # CLASS-LEVEL METHOD — NOT nested in __init__
    # ----------------------------------------------------------------------
    def _seconds_since_open(self) -> float:
        now = datetime.now(self._tz)
        return (now - self._session_open_dt).total_seconds()


    # ----------------------------------------------------------------------
    async def start(self):
        self.mux.on_underlying(self._on_underlying)
        self.mux.on_option(self._on_option)
        self.mux.parent_orchestrator = self

        await self.mux.connect(self.symbols, self.expiry_map)
        self.freshness = self.mux.freshness


    # ----------------------------------------------------------------------
    async def _on_underlying(self, event):
        sym = event.get("symbol")
        price = event.get("price")
        if sym not in self.symbols or price is None:
            return

        # -------------------------------
        # CRITICAL FIX:
        # Ensure underlying ticks update freshness.
        # Without this, evaluation stalls if option NBBO lags.
        # -------------------------------
        if self.freshness and sym in self.freshness:
            try:
                now_ms = int(time.time() * 1000)
                self.freshness[sym].update(now_ms)
            except Exception:
                pass

        self.last_price[sym] = price
        self.mux.parent_orchestrator.last_price[sym] = price
        # UI
        self.ui_state.update_underlying(
            symbol=sym, price=price,
            bid=event.get("bid"), ask=event.get("ask"),
            signal=self.ui_state.underlying.get(sym,{}).get("signal"),
            strike=self.ui_state.underlying.get(sym,{}).get("strike"),
        )
        self.ui.update(
            symbol=sym, price=price,
            bid=event.get("bid"), ask=event.get("ask"),
            signal=self.ui_state.underlying[sym].get("signal"),
            strike=self.ui_state.underlying[sym].get("strike"),
        )

        await self._evaluate(sym, price)


    # ----------------------------------------------------------------------
    async def _on_option(self, event):
        sym = event.get("symbol")
        if sym not in self.symbols:
            return

        self.chain_agg.update_from_nbbo(event)

        if not self.trail.state.active:
            return

        if event.get("contract") != self.active_contract:
            return

        bid, ask = event.get("bid"), event.get("ask")
        if bid is None or ask is None:
            return

        mid = (bid+ask)/2

        if self.ui_state.trade.active:
            self.ui_state.trade.curr_price = mid
            if self.active_entry_price:
                self.ui_state.trade.pnl_pct = (
                    (mid - self.active_entry_price) / self.active_entry_price * 100
                )

        # Hard stop
        if mid <= self.active_entry_price * 0.50:
            self.logger.log_event("hard_stop_triggered", {"mid": mid})
            await self._execute_exit(sym, "hard_sl")
            return

        # Trail
        trail_res = self.trail.update(sym, mid)
        if trail_res.get("should_exit"):
            self.logger.log_event("trail_exit_triggered", trail_res)
            await self._execute_exit(sym, "trail_exit")


    # ----------------------------------------------------------------------
    async def _evaluate(self, symbol: str, price: float):
        if self.active_symbol:
            return

        vwap_data = self.vwap[symbol].update(price)
        snap = self.chain_agg.get(symbol)
        if not snap:
            return
        rows = snap.rows

        # ============================================================
        # HYDRATION GATE — must have IV/Greeks from REST snapshots
        # ============================================================
        if not any(r.get("iv") for r in rows):
            # Chain is not enriched yet → skip evaluation tick
            return

        # Freshness gating
        if self.freshness is None or symbol not in self.freshness:
            return
        # Freshness check — 180ms window during open, 400ms afterward
        now_ms = time.time() * 1000
        max_age = 180 if self._seconds_since_open() < 180 else 400

        if not self.freshness[symbol].is_fresh(now_ms, max_age):
            return

        # Primary signal
        signal = self.entry_engine.qualify({
            "symbol": symbol,
            "price": price,
            "vwap": vwap_data["vwap"],
            "vwap_dev": vwap_data["vwap_dev"],
            "vwap_dev_change": vwap_data["vwap_dev_change"],
            "slope_prev": self.vwap[symbol].last_dev,    # <--- ADD THIS
            "upvol_pct": compute_upvol_pct(rows),
            "flow_ratio": compute_flow_ratio(rows),
            "iv_change": compute_iv_change(rows),
            "skew_shift": compute_skew_shift(rows),
            "premium_ok": True,                           # <--- ADD THIS
            "seconds_since_open": self._seconds_since_open(),
        })

        if not signal:
            return

        regime = getattr(signal, "regime", getattr(signal, "type", None))
        trend_up_signal = (signal.bias=="CALL" and regime=="TREND_UP")
        trend_dn_signal = (signal.bias=="PUT"  and regime=="TREND_DN")

        self.continuation.update_trend_flags(
            trend_up=trend_up_signal,
            trend_dn=trend_dn_signal,
        )

        # Continuation
        cont = self.continuation.on_tick(
            price=price,
            vwap=vwap_data["vwap"],
            ma=self.vwap[symbol].last_dev + vwap_data["vwap"],
            ts=time.time(),
        )

        if cont == "CONTINUATION_UP":
            bias = "CALL"
        elif cont == "CONTINUATION_DN":
            bias = "PUT"
        else:
            bias = signal.bias

        if cont:
            self.logger.log_event("continuation_signal_fired", {
                "symbol": symbol,
                "continuation_type": cont,
                "primary_regime": regime,
                "price": price,
                "vwap": vwap_data["vwap"],
            })

        # Strike selection
        best = await self.selector.select_from_chain(rows, bias, price)
        if not best:
            return

        premium = best["premium"]
        contract_id = best["contract"]

        # Latency gate
        if not self.latency.precheck(best):
            return

        # Sizing
        nlv = self.engine.account_state.net_liq
        risk_dollars = nlv * self.RISK_PCT
        raw_qty = int(risk_dollars // premium)
        cap = self.CONTRACT_CAPS.get(symbol, self.DEFAULT_CAP)
        qty = max(1, min(raw_qty, cap))

        # Execute or shadow
        take_profit = round(premium * 2.0, 2)
        stop_loss  = round(premium * 0.50, 2)

        if self.trade_mode != "shadow":
            order = await self.engine.send_bracket(
                symbol=symbol,
                side=bias,
                qty=qty,
                entry_price=premium,
                take_profit=take_profit,
                stop_loss=stop_loss,
                meta={"strike": best["strike"]},
            )
            self.logger.log_event("entry_order", order)
        else:
            self.logger.log_event("shadow_entry", {
                "symbol": symbol,
                "bias": bias,
                "premium": premium,
                "qty": qty,
            })

        # Activate trade state
        self.active_symbol  = symbol
        self.active_bias    = bias
        self.active_contract = contract_id
        self.active_entry_price = premium
        self.active_qty = qty

        self.trail.start(symbol, premium)
        self.ui.set_status(f"{symbol}: entered {bias} @ {premium:.2f}")
