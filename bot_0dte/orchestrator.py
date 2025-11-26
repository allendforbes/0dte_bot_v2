"""
Elite Orchestrator — Unified WS-Native Breakout Engine
------------------------------------------------------

Responsibilities:
    • Consume underlying ticks from MassiveMux
    • Compute VWAP (rolling window)
    • Enrich snapshot for Elite Entry Engine
    • Generate elite-level breakout signals
    • Enforce one-trade-at-a-time regime
    • Select optimal convex strike
    • Validate latency conditions
    • Execute entry (market order)
    • Maintain adaptive trailing logic
    • Apply immediate -50% catastrophic SL
    • Exit on trail or SL via market order
    • Lifecycle logging for full audit visibility

Architecture:
    MassiveMux → _on_underlying() → VWAP → EliteEntry → Execution
    MassiveMux → _on_option() → trail update → exit check
"""

import asyncio
import datetime as dt
import time
from typing import Dict, Any, List, Optional

# Strategy
from bot_0dte.strategy.elite_entry import EliteEntryEngine, EliteSignal
from bot_0dte.strategy.latency_precheck import LatencyPrecheck
from bot_0dte.strategy.strike_selector import StrikeSelector

# Risk
from bot_0dte.risk.trail_logic import TrailLogic

# UI
from bot_0dte.ui.live_panel import LivePanel

# Infra
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry

# Sizing
from bot_0dte.sizing import size_from_equity


# =====================================================================
# VWAP Tracker
# =====================================================================
class VWAPTracker:
    """Rolling VWAP calculator with last_vwap + last_vwap_dev for smoke-test compatibility."""

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.prices = []
        self.volumes = []

        # New attributes required by smoke_test.py
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

        # Required by tests
        self.last_vwap = vwap
        self.last_vwap_dev = dev

        return {
            "vwap": vwap,
            "vwap_dev": dev,
            "vwap_dev_change": change,
        }


# =====================================================================
# Chain Aggregator
# =====================================================================
class ChainAggregator:
    """NBBO → normalized chain snapshot."""

    def __init__(self, symbols):
        self.cache = {s: {} for s in symbols}
        self.last_ts = {s: 0.0 for s in symbols}

    def update(self, event):
        sym = event.get("symbol")
        contract = event.get("contract")
        if not sym or not contract:
            return
        self.cache[sym][contract] = event
        self.last_ts[sym] = time.time()

    def is_fresh(self, symbol, threshold=2.0):
        return (time.time() - self.last_ts.get(symbol, 0)) <= threshold

    def get_chain(self, symbol):
        out = []
        for row in self.cache.get(symbol, {}).values():
            bid = row.get("bid")
            ask = row.get("ask")
            if bid is None or ask is None:
                continue
            mid = (bid + ask) / 2
            out.append(
                {
                    "symbol": symbol,
                    "strike": row.get("strike"),
                    "right": row.get("right"),
                    "premium": mid,
                    "bid": bid,
                    "ask": ask,
                    "contract": row.get("contract"),
                }
            )
        return out


# =====================================================================
# Orchestrator
# =====================================================================
class Orchestrator:
    """
    Elite orchestrator — one active trade at a time, trail-only exit.
    """

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

        # Universe
        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        # Prices
        self.last_price = {s: None for s in self.symbols}

        # VWAP
        self.vwap = {s: VWAPTracker() for s in self.symbols}

        # --- REQUIRED FOR SMOKE TEST COMPATIBILITY ---
        # Smoke test expects ._vwap_tracker["SPY"] to exist
        self._vwap_tracker = self.vwap
        # ----------------------------------------------

        # Chain aggregator
        self.chain_agg = ChainAggregator(self.symbols)

        # Strategies & logic
        self.entry_engine = EliteEntryEngine()
        self.latency = LatencyPrecheck()
        self.selector = StrikeSelector(chain_bridge=None, engine=self.engine)
        self.trail = TrailLogic(max_loss_pct=0.50)

        # UI
        self.ui = LivePanel()

        # Active trade state
        self.active_symbol: Optional[str] = None
        self.active_contract: Optional[str] = None
        self.active_bias: Optional[str] = None
        self.active_entry_price: Optional[float] = None
        self.active_qty: Optional[int] = None

        print("\n" + "=" * 70)
        print(" ELITE ORCHESTRATOR INITIALIZED ".center(70, "="))
        print("=" * 70 + "\n")

    # ===============================================================
    async def start(self):
        self.mux.on_underlying(self._on_underlying)
        self.mux.on_option(self._on_option)
        await self.mux.connect(self.symbols, self.expiry_map)

    # ===============================================================
    async def _on_underlying(self, event):
        sym = event.get("symbol")
        price = event.get("price")
        if sym not in self.symbols or price is None:
            return

        self.last_price[sym] = price

        self.ui.update(
            symbol=sym,
            price=price,
            bid=event.get("bid"),
            ask=event.get("ask"),
        )

        await self._evaluate(sym, price)

    # ===============================================================
    async def _on_option(self, event):
        """Option NBBO → trail updates + SL enforcement."""
        sym = event.get("symbol")
        if sym not in self.symbols:
            return

        self.chain_agg.update(event)

        # No active trade?
        if not self.trail.state.active:
            return

        # Contract not yet initialized or mismatch
        if not self.active_contract or event.get("contract") != self.active_contract:
            return

        bid = event.get("bid")
        ask = event.get("ask")
        if bid is None or ask is None:
            return

        mid = (bid + ask) / 2

        # -------------------------------
        # Hard SL at -50%
        # -------------------------------
        if (
            self.active_entry_price is not None
            and mid <= self.active_entry_price * 0.50
        ):
            self.logger.log_event("hard_stop_triggered", {"mid": mid})
            await self._execute_exit(sym, reason="hard_sl")
            return

        # -------------------------------
        # Trail update
        # -------------------------------
        trail_res = self.trail.update(sym, mid)

        if trail_res.get("should_exit"):
            self.logger.log_event("trail_exit_triggered", trail_res)
            await self._execute_exit(sym, reason="trail_exit")

    # ===============================================================
    async def _evaluate(self, symbol: str, price: float):
        """Main entry evaluation path."""

        # No new trades while active
        if self.active_symbol:
            return

        # VWAP enrichment
        vwap_data = self.vwap[symbol].update(price)

        snap = {
            "symbol": symbol,
            "price": price,
            "vwap": vwap_data["vwap"],
            "vwap_dev": vwap_data["vwap_dev"],
            "vwap_dev_change": vwap_data["vwap_dev_change"],
            "upvol_pct": None,
            "flow_ratio": None,
            "iv_change": None,
            "skew_shift": None,
            "seconds_since_open": self._seconds_since_open(),
        }

        # Signal
        sig: Optional[EliteSignal] = self.entry_engine.qualify(snap)
        if not sig:
            return

        self.logger.log_event("signal_generated", sig.__dict__)
        self.ui.set_status(f"{symbol}: elite breakout detected")

        # Chain freshness
        if not self.chain_agg.is_fresh(symbol):
            self.logger.log_event("signal_dropped", {"reason": "stale_chain"})
            return

        chain = self.chain_agg.get_chain(symbol)
        if not chain:
            self.logger.log_event("signal_dropped", {"reason": "empty_chain"})
            return

        # Strike selection
        strike = await self.selector.select_from_chain(chain, sig.bias)
        if not strike:
            self.logger.log_event("signal_dropped", {"reason": "no_strike"})
            return

        # Latency pre-check
        tick = {
            "price": strike["premium"],
            "bid": strike["bid"],
            "ask": strike["ask"],
            "vwap_dev_change": vwap_data["vwap_dev_change"],
        }

        pre = self.latency.validate(symbol, tick, sig.bias)
        if not pre.ok:
            self.logger.log_event("entry_blocked", {"reason": pre.reason})
            return

        entry_price = pre.limit_price
        if entry_price is None:
            self.logger.log_event("entry_blocked", {"reason": "no_entry_price"})
            return

        # Sizing
        premium = strike.get("premium")
        if premium is None or premium <= 0:
            self.logger.log_event("entry_blocked", {"reason": "invalid_premium"})
            return

        if not self.engine.account_state.is_fresh():
            self.logger.log_event("entry_blocked", {"reason": "stale_equity"})
            return

        qty = size_from_equity(self.engine.account_state.net_liq, premium)

        # Activate trail
        self.trail.initialize(symbol, entry_price, sig.trail_mult)

        # Set active trade
        self.active_symbol = symbol
        self.active_contract = strike["contract"]
        self.active_bias = sig.bias
        self.active_entry_price = entry_price
        self.active_qty = qty

        # EXECUTION -------------------------------------------
        if not self.auto and not self.active_symbol:
            self.logger.log_event("trade_blocked", {"reason": "auto_trade_off"})
            return

        if self.trade_mode == "shadow":
            self.logger.log_event(
                "shadow_entry",
                {
                    "symbol": symbol,
                    "contract": self.active_contract,
                    "entry": entry_price,
                    "qty": qty,
                    "bias": sig.bias,
                    "regime": sig.regime,
                    "grade": sig.grade,
                    "score": sig.score,
                },
            )
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
                "trail_mult": sig.trail_mult,
            },
        )
        self.logger.log_event("entry_order", order)

    # ===============================================================
    async def _execute_exit(self, symbol: str, reason: str):
        """Process exit path for active trade."""

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

        # Reset active state
        self.active_symbol = None
        self.active_contract = None
        self.active_bias = None
        self.active_entry_price = None
        self.active_qty = None
        self.trail.state.active = False

        self.ui.set_status(f"{symbol}: exited ({reason})")

    # ===============================================================
    def _seconds_since_open(self):
        now = dt.datetime.now().astimezone()
        open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
        return max(0.0, (now - open_t).total_seconds())
