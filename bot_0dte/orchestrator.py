# orchestrator.py — MassiveMux WS-Native Orchestrator (Full Replacement)
# NOTE: This is a production-grade scaffold. Integrate with MassiveMux,
# StocksWSAdapter, OptionsWSAdapter, ExecutionEngine, and StrikeSelector.

import asyncio
import datetime as dt
import time
from typing import Dict, Any, List

from bot_0dte.strategy.morning_breakout import MorningBreakout
from bot_0dte.strategy.latency_precheck import LatencyPrecheck
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.risk.trail_logic import TrailLogic
from bot_0dte.ui.live_panel import LivePanel
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.sizing import size_from_equity


# =====================================================================
# Chain Aggregator (Massive NBBO → normalized chain snapshot)
# =====================================================================
class ChainAggregator:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.cache = {s: {} for s in symbols}
        self.last_ts = {s: 0.0 for s in symbols}

    def update(self, event: Dict[str, Any]):
        sym = event.get("symbol")
        contract = event.get("contract")
        if not sym or not contract:
            return
        self.cache[sym][contract] = event
        self.last_ts[sym] = time.time()

    def is_fresh(self, symbol: str, threshold: float = 2.0) -> bool:
        return (time.time() - self.last_ts[symbol]) <= threshold

    def get_chain(self, symbol: str) -> List[Dict[str, Any]]:
        out = []
        for row in self.cache[symbol].values():
            mid = (row.get("bid", 0) + row.get("ask", 0)) / 2
            out.append(
                {
                    "symbol": symbol,
                    "strike": row.get("strike"),
                    "right": row.get("right"),
                    "premium": mid,
                    "bid": row.get("bid"),
                    "ask": row.get("ask"),
                    "contract": row.get("contract"),
                }
            )
        return out


# =====================================================================
# Orchestrator
# =====================================================================
class Orchestrator:
    def __init__(
        self,
        engine,
        mux,
        telemetry: Telemetry,
        logger: StructuredLogger,
        universe=None,
        auto_trade_enabled=False,
        trade_mode="shadow",  # shadow / paper / live
    ):
        self.engine = engine
        self.mux = mux
        self.logger = logger
        self.telemetry = telemetry
        self.auto_trade_enabled = auto_trade_enabled
        self.trade_mode = trade_mode

        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        self.last_price = {s: None for s in self.symbols}
        self.last_underlying_ts = {s: 0.0 for s in self.symbols}

        self.chain_agg = ChainAggregator(self.symbols)

        self.engine.expiry_map = self.expiry_map

        self.breakout = MorningBreakout(telemetry=self.telemetry)
        self.latency = LatencyPrecheck()
        self.trail = TrailLogic(max_loss_pct=0.50)
        self.selector = StrikeSelector(chain_bridge=None, engine=self.engine)

        self.ui = LivePanel()

        print("\n" + "=" * 70)
        print("WS-NATIVE ORCHESTRATOR INITIALIZED".center(70))
        print("=" * 70 + "\n")

    # ------------------------------------------------------------
    async def start(self):
        self.mux.on_underlying(self._on_underlying)
        self.mux.on_option(self._on_option)
        await self.mux.connect(self.symbols)

    # ------------------------------------------------------------
    async def _on_underlying(self, event: Dict[str, Any]):
        sym = event.get("symbol")
        price = event.get("price")
        if sym not in self.symbols or price is None:
            return
        self.last_price[sym] = price
        self.last_underlying_ts[sym] = time.time()

        self.ui.update(symbol=sym, price=price)
        await self._evaluate(sym)

    # ------------------------------------------------------------
    async def _on_option(self, event: Dict[str, Any]):
        sym = event.get("symbol")
        if sym in self.symbols:
            self.chain_agg.update(event)

    # ------------------------------------------------------------
    async def _evaluate(self, symbol: str):
        price = self.last_price[symbol]
        if price is None:
            return

        # breakout signal
        sig = self.breakout.qualify(
            {
                "symbol": symbol,
                "price": price,
                "vwap": price,
                "vwap_dev": 0,
                "vwap_dev_change": 0,
                "seconds_since_open": self._seconds_since_open(),
            }
        )
        if not sig:
            return

        self.logger.log_event("signal_generated", sig)
        self.ui.set_status(f"{symbol}: breakout detected")

        # chain freshness
        if not self.chain_agg.is_fresh(symbol):
            self.logger.log_event("signal_dropped", {"reason": "stale_chain"})
            return

        chain = self.chain_agg.get_chain(symbol)
        if not chain:
            self.logger.log_event("signal_dropped", {"reason": "empty_chain"})
            return

        strike = await self.selector.select_from_chain(chain, sig["bias"])
        if not strike:
            self.logger.log_event("signal_dropped", {"reason": "no_strike"})
            return

        pre = self.latency.validate(symbol, {"price": price}, sig["bias"])
        if not pre.ok:
            self.logger.log_event("entry_blocked", {"reason": pre.reason})
            return

        if not self.engine.account_state.is_fresh():
            self.logger.log_event("entry_blocked", {"reason": "stale_equity"})
            return

        qty = size_from_equity(self.engine.account_state.net_liq, price)
        prem = strike["premium"]
        tp = prem * sig["tp_mult"]
        sl = prem * sig["sl_mult"]

        self.trail.initialize(symbol, pre.limit_price, sig["trail_mult"])

        if not self.auto_trade_enabled:
            self.logger.log_event("trade_blocked", {"reason": "auto_trade_off"})
            return

        if self.trade_mode == "shadow":
            self.logger.log_event(
                "shadow_trade",
                {
                    "symbol": symbol,
                    "strike": strike,
                    "entry": pre.limit_price,
                    "tp": tp,
                    "sl": sl,
                    "qty": qty,
                },
            )
            return

        if self.trade_mode == "paper":
            order = await self.engine.mock_bracket(
                symbol, sig["bias"], qty, pre.limit_price, tp, sl
            )
            self.logger.log_event("paper_order", order)
            return

        if self.trade_mode == "live":
            order = await self.engine.send_bracket(
                symbol=symbol,
                side=sig["bias"],
                qty=qty,
                entry_price=pre.limit_price,
                take_profit=tp,
                stop_loss=sl,
                meta={
                    "regime": sig["regime"],
                    "grade": sig["grade"],
                    "vol_path": sig["vol_path"],
                },
            )
            self.logger.log_event("order_submitted", order)
            return

    # ------------------------------------------------------------
    def _seconds_since_open(self) -> float:
        now = dt.datetime.now().astimezone()
        open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
        return max(0.0, (now - open_t).total_seconds())
