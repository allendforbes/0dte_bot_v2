"""
Orchestrator — WS-Native Event Router + VWAP Enrichment

Responsibilities:
    • Consume underlying ticks from MassiveMux
    • Compute VWAP locally (rolling window)
    • Enrich snapshots with vwap, vwap_dev, vwap_dev_change
    • Route to strategy stack (MorningBreakout → LatencyPrecheck → StrikeSelector)
    • Aggregate option chain from NBBO ticks
    • Execute trades via ExecutionEngine

Architecture:
    MassiveMux → Orchestrator._on_underlying() → VWAP enrichment → Strategy → Execution
    MassiveMux → Orchestrator._on_option() → ChainAggregator → StrikeSelector
"""

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
# VWAP Tracker (rolling window calculation)
# =====================================================================
class VWAPTracker:
    """
    Compute VWAP from tick stream.
    Maintains rolling window of last N ticks.
    """

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.prices = []
        self.volumes = []
        self.last_vwap = None
        self.last_dev = 0.0

    def update(self, price: float, volume: float = 1.0) -> Dict[str, float]:
        """
        Update VWAP with new tick.

        Args:
            price: Current price
            volume: Tick volume (defaults to 1 for tick count)

        Returns:
            {vwap, vwap_dev, vwap_dev_change}
        """
        self.prices.append(price)
        self.volumes.append(volume)

        # Maintain window size
        if len(self.prices) > self.window_size:
            self.prices.pop(0)
            self.volumes.pop(0)

        # Calculate VWAP
        total_pv = sum(p * v for p, v in zip(self.prices, self.volumes))
        total_v = sum(self.volumes)
        vwap = total_pv / total_v if total_v > 0 else price

        # Calculate deviation and change
        vwap_dev = price - vwap
        prev_dev = self.last_dev
        vwap_dev_change = vwap_dev - prev_dev

        # Store for next iteration
        self.last_vwap = vwap
        self.last_dev = vwap_dev

        return {
            "vwap": vwap,
            "vwap_dev": vwap_dev,
            "vwap_dev_change": vwap_dev_change,
        }


# =====================================================================
# Chain Aggregator (Massive NBBO → normalized chain snapshot)
# =====================================================================
class ChainAggregator:
    """
    Aggregate NBBO ticks into chain snapshot.
    Maintains cache of latest bid/ask for each contract.
    """

    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.cache = {s: {} for s in symbols}
        self.last_ts = {s: 0.0 for s in symbols}

    def update(self, event: Dict[str, Any]):
        """
        Update chain cache with NBBO tick.

        Event format (from MassiveMux):
        {
            "symbol": str,
            "contract": str,
            "strike": float,
            "right": "C" | "P",
            "bid": float,
            "ask": float,
            "_recv_ts": float
        }
        """
        sym = event.get("symbol")
        contract = event.get("contract")
        if not sym or not contract:
            return

        self.cache[sym][contract] = event
        self.last_ts[sym] = time.time()

    def is_fresh(self, symbol: str, threshold: float = 2.0) -> bool:
        """Check if chain data is recent enough."""
        return (time.time() - self.last_ts.get(symbol, 0)) <= threshold

    def get_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Get normalized chain for strike selector.

        Returns list of dicts:
        {
            "symbol": str,
            "strike": float,
            "right": "C" | "P",
            "premium": float (mid price),
            "bid": float,
            "ask": float,
            "contract": str (OCC code)
        }
        """
        out = []
        for row in self.cache.get(symbol, {}).values():
            bid = row.get("bid", 0)
            ask = row.get("ask", 0)
            mid = (bid + ask) / 2 if (bid and ask) else 0

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
# Orchestrator — WS-Native Event Router
# =====================================================================
class Orchestrator:
    """
    Central orchestrator for WS-native bot.

    Flow:
        1. Receive underlying tick → compute VWAP → enrich snapshot
        2. Pass to strategy → get signal
        3. Check chain freshness → select strike
        4. Validate latency → size position
        5. Execute trade
    """

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

        # Universe & expiry
        self.symbols = universe or get_universe_for_today()
        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        # State
        self.last_price = {s: None for s in self.symbols}
        self.last_underlying_ts = {s: 0.0 for s in self.symbols}

        # VWAP tracking (NEW)
        self._vwap_tracker = {s: VWAPTracker(window_size=100) for s in self.symbols}

        # Chain aggregator
        self.chain_agg = ChainAggregator(self.symbols)

        # Strategy stack
        self.breakout = MorningBreakout(telemetry=self.telemetry)
        self.latency = LatencyPrecheck()
        self.selector = StrikeSelector(chain_bridge=None, engine=self.engine)
        self.trail = TrailLogic(max_loss_pct=0.50)

        # UI
        self.ui = LivePanel()

        print("\n" + "=" * 70)
        print(" MASSIVE-WS ORCHESTRATOR INITIALIZED ".center(70, "="))
        print("=" * 70 + "\n")

    # ==================================================================
    async def start(self):
        """
        Attach MassiveMux event handlers and start WS connections.
        """
        # Register callbacks
        self.mux.on_underlying(self._on_underlying)
        self.mux.on_option(self._on_option)

        # Start MassiveMux (connects both WS, subscribes underlyings)
        await self.mux.connect(self.symbols, self.expiry_map)

    # ==================================================================
    async def _on_underlying(self, event: Dict[str, Any]):
        """
        Handle underlying tick from MassiveMux.

        Event format:
        {
            "symbol": str,
            "price": float,
            "bid": float | None,
            "ask": float | None,
            "_recv_ts": float
        }
        """
        sym = event.get("symbol")
        price = event.get("price")

        if sym not in self.symbols or price is None:
            return

        # Update state
        self.last_price[sym] = price
        self.last_underlying_ts[sym] = time.time()

        # Update UI
        try:
            self.ui.update(
                symbol=sym, price=price, bid=event.get("bid"), ask=event.get("ask")
            )
        except Exception as e:
            self.logger.warn(f"[UI] update failed: {e}")

        # Evaluate for signal
        await self._evaluate(sym, price)

    # ==================================================================
    async def _on_option(self, event: Dict[str, Any]):
        """
        Handle option NBBO tick from MassiveMux.

        Event format:
        {
            "symbol": str,
            "contract": str,
            "strike": float,
            "right": "C" | "P",
            "bid": float,
            "ask": float,
            "_recv_ts": float
        }
        """
        sym = event.get("symbol")
        if sym in self.symbols:
            self.chain_agg.update(event)

    # ==================================================================
    async def _evaluate(self, symbol: str, price: float):
        """
        Evaluate signal for given symbol/price.

        Steps:
            1. Compute VWAP + enrichment
            2. Pass to strategy → get signal
            3. Validate chain freshness
            4. Select strike
            5. Validate latency
            6. Size position
            7. Execute trade
        """

        # ============================================================
        # STEP 1: VWAP ENRICHMENT (NEW)
        # ============================================================
        tracker = self._vwap_tracker[symbol]
        vwap_data = tracker.update(price)

        # Build enriched snapshot for strategy
        enriched_snap = {
            "symbol": symbol,
            "price": price,
            "vwap": vwap_data["vwap"],
            "vwap_dev": vwap_data["vwap_dev"],
            "vwap_dev_change": vwap_data["vwap_dev_change"],
            "upvol_pct": None,  # Not available from WS
            "flow_ratio": None,  # Not available from WS
            "iv_change": None,  # Not available from WS
            "skew_shift": None,  # Not available from WS
            "seconds_since_open": self._seconds_since_open(),
        }

        # ============================================================
        # STEP 2: STRATEGY SIGNAL
        # ============================================================
        sig = self.breakout.qualify(enriched_snap)

        if not sig:
            return

        self.logger.log_event("signal_generated", sig)
        self.ui.set_status(f"{symbol}: breakout detected")

        # ============================================================
        # STEP 3: CHAIN FRESHNESS CHECK
        # ============================================================
        if not self.chain_agg.is_fresh(symbol):
            self.logger.log_event("signal_dropped", {"reason": "stale_chain"})
            return

        chain = self.chain_agg.get_chain(symbol)
        if not chain:
            self.logger.log_event("signal_dropped", {"reason": "empty_chain"})
            return

        # ============================================================
        # STEP 4: STRIKE SELECTION
        # ============================================================
        strike = await self.selector.select_from_chain(chain, sig["bias"])
        if not strike:
            self.logger.log_event("signal_dropped", {"reason": "no_strike"})
            return

        # ============================================================
        # STEP 5: LATENCY PRE-CHECK
        # ============================================================
        # Build tick dict for latency check
        tick_data = {
            "price": strike["premium"],
            "bid": strike["bid"],
            "ask": strike["ask"],
            "vwap_dev_change": vwap_data["vwap_dev_change"],
        }

        pre = self.latency.validate(symbol, tick_data, sig["bias"])
        if not pre.ok:
            self.logger.log_event("entry_blocked", {"reason": pre.reason})
            return

        # ============================================================
        # STEP 6: POSITION SIZING
        # ============================================================
        if not self.engine.account_state.is_fresh():
            self.logger.log_event("entry_blocked", {"reason": "stale_equity"})
            return

        qty = size_from_equity(self.engine.account_state.net_liq, strike["premium"])

        # ============================================================
        # STEP 7: TP/SL CALCULATION
        # ============================================================
        prem = strike["premium"]
        tp = prem * sig["tp_mult"]
        sl = prem * sig["sl_mult"]

        self.trail.initialize(symbol, pre.limit_price, sig["trail_mult"])

        # ============================================================
        # STEP 8: EXECUTION
        # ============================================================
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
                symbol,
                sig["bias"],
                qty,
                pre.limit_price,
                tp,
                sl,
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

    # ==================================================================
    def _seconds_since_open(self) -> float:
        """Calculate seconds since market open (9:30 AM ET)."""
        now = dt.datetime.now().astimezone()
        open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
        return max(0.0, (now - open_t).total_seconds())
