import asyncio
import datetime as dt
from typing import Dict, Any

from bot_0dte.strategy.morning_breakout import MorningBreakout
from bot_0dte.strategy.latency_precheck import LatencyPrecheck
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.risk.trail_logic import TrailLogic
from bot_0dte.ui.live_panel import LivePanel
from bot_0dte.universe import get_universe_for_today
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry
from bot_0dte.sizing import size_from_equity


class Orchestrator:
    """
    Clean, modern orchestrator.
    SessionController constructs:
        - engine
        - chain_bridge
        - feed
        - logger
        - telemetry

    Orchestrator:
        - receives converted ticks
        - runs breakout → selector → latency → sizing → Mode-B
        - builds strict execution request
        - sends to ExecutionEngine
        - updates UI panel
    """

    def __init__(
        self,
        engine,
        chain_bridge,
        feed,
        telemetry: Telemetry,
        logger: StructuredLogger,
        universe=None,
    ):
        self.engine = engine
        self.bridge = chain_bridge
        self.feed = feed
        self.logger = logger
        self.telemetry = telemetry

        # Universe
        self.symbols = universe or get_universe_for_today()

        # Expiry and last price caches (MA-based)
        from bot_0dte.universe import get_expiry_for_symbol

        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}
        self.md_chain_cache = {s: [] for s in self.symbols}
        self.last_price = {s: None for s in self.symbols}

        # Inject shared state into engine
        self.engine.expiry_map = self.expiry_map
        self.engine.last_price = self.last_price
        self.engine.md_chain_cache = self.md_chain_cache

        self.feed = feed
        self.logger = logger
        self.telemetry = telemetry

        self.symbols = universe or get_universe_for_today()
        # Expiry and last price caches
        from bot_0dte.universe import get_expiry_for_symbol

        self.expiry_map = {s: get_expiry_for_symbol(s) for s in self.symbols}

        # --- Strategy stack ---
        self.breakout = MorningBreakout(telemetry=self.telemetry)
        self.latency = LatencyPrecheck()
        self.trail = TrailLogic(max_loss_pct=0.50)

        # --- Selector will use chain_bridge internally ---
        self.selector = StrikeSelector(chain_bridge=self.bridge, engine=self.engine)

        # --- UI panel ---
        self.ui = LivePanel()

        print("\n" + "=" * 70)
        print("🚀 ORCHESTRATOR INITIALIZED".center(70))
        print("=" * 70)
        print(f"📅 {dt.datetime.now().strftime('%A, %Y-%m-%d')}")
        print(f"📈 Universe: {', '.join(self.symbols)}")
        print("=" * 70 + "\n")

    # -------------------------------------------------------------
    # START orchestrator (called by SessionController.run())
    # -------------------------------------------------------------
    async def start(self):
        """
        Bind callback and start market data feed.
        SessionController does NOT loop here — feed drives everything.
        """
        print("[ORCH] Starting MarketDataFeed...")
        self.feed.callback = self.on_market_data
        await self.feed.start(self.symbols)

    # -------------------------------------------------------------
    # MAIN MARKET DATA INGESTION
    # -------------------------------------------------------------
    async def on_market_data(self, tick: Dict[str, Any]):
        """
        tick format (new feed):
            {
              "symbol": "SPY",
              "price": ...,
              "bid": ...,
              "ask": ...,
              "vwap": ...,
              "upvol_pct": ...,
              "flow_ratio": ...,
              ...
            }
        """
        try:
            symbol = tick["symbol"]

            # Update price + chain caches
            self.last_price[symbol] = tick.get("price")
            if "chain" in tick:
                self.md_chain_cache[symbol] = tick["chain"]

            # --- UI update: raw tick ---
            self.ui.update(
                symbol=symbol,
                price=tick["price"],
                bid=tick.get("bid"),
                ask=tick.get("ask"),
                signal=None,
                strike=None,
                expiry=None,
            )

            # =========================================================
            # STEP 1: MORNING BREAKOUT
            # =========================================================
            signal = self.breakout.qualify(
                {
                    "symbol": symbol,
                    "price": tick["price"],
                    "vwap": tick.get("vwap"),
                    "vwap_dev": tick["price"] - tick.get("vwap", tick["price"]),
                    "vwap_dev_change": tick.get("vwap_dev_change", 0),
                    "upvol_pct": tick.get("upvol_pct"),
                    "flow_ratio": tick.get("flow_ratio"),
                    "iv_change": tick.get("iv_change"),
                    "skew_shift": tick.get("skew_shift"),
                    "seconds_since_open": tick.get(
                        "seconds_since_open", self._seconds_since_open()
                    ),
                }
            )

            if not signal:
                return

            self.ui.update(
                symbol=symbol,
                price=tick["price"],
                bid=tick.get("bid"),
                ask=tick.get("ask"),
                signal=signal,
                strike=None,
            )
            self.logger.log_event("signal_generated", signal)

            # =========================================================
            # STEP 2: STRICT STRIKE SELECTION (MarketData.app chain)
            # =========================================================
            self.ui.set_status(f"{symbol}: selecting best strike…")
            strike = await self.selector.select(symbol, signal["bias"])

            if not strike:
                self.logger.log_event("signal_dropped", {"reason": "no_strike"})
                return

            self.ui.update(
                symbol=symbol,
                price=tick["price"],
                bid=strike["bid"],
                ask=strike["ask"],
                signal=signal,
                strike=strike,
            )
            self.ui.set_status(
                f"{symbol}: strike {strike['strike']}{strike['right']} prem={strike['premium']}"
            )
            self.logger.log_event("strike_selected", strike)

            # =========================================================
            # STEP 3: LATENCY PRECHECK
            # =========================================================
            self.ui.set_status(f"{symbol}: latency precheck…")
            pre = self.latency.validate(symbol, tick, signal["bias"])

            if not pre.ok:
                self.ui.set_status(f"{symbol}: blocked – {pre.reason}")
                self.logger.log_event("entry_blocked", {"reason": pre.reason})
                return

            # =========================================================
            # STEP 4: SIZING
            # =========================================================
            nlv = self.engine.account_state.net_liq

            if not self.engine.account_state.is_fresh():
                self.logger.log_event("entry_blocked", {"reason": "stale_equity"})
                return

            qty = size_from_equity(nlv, tick["price"])
            self.ui.set_status(f"{symbol}: sizing = {qty}…")

            # =========================================================
            # STEP 5: TRAIL LOGIC INIT (meta)
            # =========================================================
            self.trail.initialize(
                symbol=symbol,
                entry_price=pre.limit_price,
                mult=signal["trail_mult"],
            )

            # =========================================================
            # STEP 6: MODE-B USER CONFIRMATION
            # =========================================================
            prem = strike["premium"]
            tp_mult = signal["tp_mult"]
            sl_mult = signal["sl_mult"]

            tp = prem * tp_mult
            sl = prem * sl_mult

            print("")
            print("=" * 60)
            print(f"🚨 TRADE SIGNAL DETECTED for {symbol}")
            print(f"Bias:         {signal['bias']}")
            print(f"Regime:       {signal['regime']}")
            print(f"Grade:        {signal['grade']}")
            print(f"Vol Path:     {signal['vol_path']}")
            print("-" * 60)
            print(f"Strike:       {strike['strike']} {strike['right']}")
            print(f"Premium:      {prem:.2f}")
            print(f"Entry LMT:    {pre.limit_price:.2f}")
            print(f"Take Profit:  {tp:.2f}")
            print(f"Stop Loss:    {sl:.2f}")
            print(f"Qty:          {qty}")
            print("=" * 60)

            resp = await asyncio.to_thread(input, "Approve trade? (y/n): ")
            if resp.strip().lower() not in ("y", "yes"):
                print("❌ Trade rejected.")
                return

            # =========================================================
            # STEP 7: BUILD STRICT EXECUTION REQUEST
            # =========================================================
            req = {
                "symbol": symbol,
                "expiry": strike["expiry"],
                "strike": strike["strike"],
                "right": strike["right"],
                "qty": qty,
                "side": signal["bias"],
                "take_profit": tp,
                "stop_loss": sl,
            }

            # =========================================================
            # STEP 8: EXECUTION (ENGINE)
            # =========================================================
            order = await self.engine.send_bracket(
                symbol=symbol,
                side=signal["bias"],
                qty=qty,
                entry_price=pre.limit_price,
                take_profit=tp,
                stop_loss=sl,
                meta={
                    "regime": signal["regime"],
                    "grade": signal["grade"],
                    "vol_path": signal["vol_path"],
                },
            )
            self.logger.log_event("order_submitted", order)

            # =========================================================
            # POST-EXECUTION STATUS PANEL
            # =========================================================
            print("")
            print("=" * 65)
            print("🎯 EXECUTION CONFIRMED".center(65))
            print("=" * 65)

            print(f"Symbol:        {symbol}")
            print(f"Bias:          {signal['bias']}")
            print(f"Regime:        {signal['regime']}")
            print(f"Grade:         {signal['grade']}")
            print(f"Vol Path:      {signal['vol_path']}")
            print("-" * 65)
            print(f"Entry Filled:  {pre.limit_price:.2f}")
            print(f"Take Profit:   {tp:.2f}")
            print(f"Stop Loss:     {sl:.2f}")
            print(f"Qty:           {qty}")
            print(f"Trail Mode:    ACTIVE (mult={signal['trail_mult']})")
            print("-" * 65)
            print(f"Order ID:      {order.get('entry_order_id')}")
            print(f"TP Order ID:   {order.get('tp_order_id')}")
            print(f"SL Order ID:   {order.get('sl_order_id')}")
            print("=" * 65)
            print("")

        except Exception as e:
            self.logger.log_event("orch_error", {"error": str(e)})
            print(f"[ORCH][ERROR] {e}")
            raise

    # -------------------------------------------------------------
    def _seconds_since_open(self) -> float:
        """Seconds since 9:30 ET."""
        now = dt.datetime.now().astimezone()
        open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
        return max(0, (now - open_t).total_seconds())
