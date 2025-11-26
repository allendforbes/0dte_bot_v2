import time
import shutil
import datetime as dt


# ============================================================
# ANSI Coloring
# ============================================================
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


# ============================================================
# Live Panel
# ============================================================
class LivePanel:
    """
    Lightweight ASCII market dashboard with three sections:

      1. Underlying Panel        — live underlying ticks
      2. Active Trade Panel      — current trade state
      3. Lifecycle Log Panel     — important workflow events

    Async-safe: rendering is throttled and non-blocking.
    """

    def __init__(self):
        self.last_render = 0
        self.min_interval = 0.25
        self.rows = {}

        # Rolling lifecycle log
        self.log_buffer = []
        self.max_logs = 30

        # UIState (attached by orchestrator)
        self.ui_state = None

        print("[UI] LivePanel ready.")

    # ---------------------------------------------------------
    def attach_ui_state(self, ui_state):
        """Attach UIState. Must be called by the orchestrator."""
        self.ui_state = ui_state

    # ---------------------------------------------------------
    def set_status(self, msg: str):
        print(f"[STATUS] {msg}")

    # ---------------------------------------------------------
    def log_event(self, msg: str, color=None):
        """Append to lifecycle log with auto-color detection."""
        if color is None:
            text = msg.lower()
            if "exit" in text or "tp" in text:
                color = C.GREEN
            elif "sl" in text or "stop" in text:
                color = C.RED
            elif "pnl" in text:
                color = C.GREEN if "+" in text else C.RED
            else:
                color = C.CYAN

        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {color}{msg}{C.RESET}"

        self.log_buffer.append(entry)
        if len(self.log_buffer) > self.max_logs:
            self.log_buffer.pop(0)

    # ---------------------------------------------------------
    def update(
        self,
        symbol: str,
        price: float,
        bid: float | None = None,
        ask: float | None = None,
        signal: dict | None = None,
        strike: dict | None = None,
        expiry: str | None = None,
    ):
        """Store latest market row and render if interval passed."""
        self.rows[symbol] = {
            "symbol": symbol,
            "price": price,
            "bid": bid,
            "ask": ask,
            "signal": signal,
            "strike": strike,
            "expiry": expiry,
        }

        now = time.time()
        if now - self.last_render >= self.min_interval:
            self.last_render = now
            self.render()

    # ==========================================================
    # Formatting Helpers
    # ==========================================================
    def _fmt_price(self, p):
        if p is None:
            return f"{C.DIM}--{C.RESET}"
        return f"{C.BOLD}{p:.2f}{C.RESET}"

    def _fmt_signal(self, sig):
        if sig is None:
            return f"{C.DIM}--{C.RESET}"

        bias = sig["bias"]
        color = C.GREEN if bias == "CALL" else C.RED
        grade = sig.get("grade", "?")
        regime = sig.get("regime", "?")

        return f"{color}{bias}{C.RESET} ({grade}, {regime})"

    def _fmt_strike(self, st):
        if st is None:
            return f"{C.DIM}--{C.RESET}"
        return f"{C.CYAN}{st['strike']}{st['right']}{C.RESET} @ {st['premium']:.2f}"

    # ==========================================================
    # Rendering
    # ==========================================================
    def render(self):
        term_width = shutil.get_terminal_size((120, 30)).columns

        # Clear + move cursor home
        print("\033[2J\033[H", end="")

        # ------------------------------------------------------
        # PANEL 1 — MARKET PANEL
        # ------------------------------------------------------
        print("=" * term_width)
        print("📡  LIVE MARKET PANEL".center(term_width))
        print("=" * term_width)
        print(
            f"{'Symbol':<8} {'Price':<10} {'Bid':<10} {'Ask':<10} "
            f"{'Signal':<25} {'Strike':<20}"
        )
        print("-" * term_width)

        for sym, row in sorted(self.rows.items()):
            print(
                f"{sym:<8} "
                f"{self._fmt_price(row['price']):<10} "
                f"{self._fmt_price(row['bid']):<10} "
                f"{self._fmt_price(row['ask']):<10} "
                f"{self._fmt_signal(row['signal']):<25} "
                f"{self._fmt_strike(row['strike']):<20}"
            )

        print("=" * term_width)
        print(f"{C.DIM}Updated {dt.datetime.now().strftime('%H:%M:%S')}{C.RESET}")

        # ------------------------------------------------------
        # PANEL 2 — ACTIVE TRADE
        # ------------------------------------------------------
        if self.ui_state and self.ui_state.trade.active:
            self.render_trade_panel(self.ui_state.trade)

        # ------------------------------------------------------
        # PANEL 3 — LIFECYCLE LOG
        # ------------------------------------------------------
        self.render_log_panel()

    # ---------------------------------------------------------
    def render_trade_panel(self, trade):
        print("\n" + "=" * 70)
        print("🟢 ACTIVE TRADE".center(70))
        print("=" * 70)

        print(f"Symbol:        {trade.symbol}")
        print(f"Contract:      {trade.contract}")
        print(f"Bias:          {trade.bias}")
        print(f"Regime:        {trade.regime}")
        print(f"Grade:         {trade.grade}")
        print(f"Strike:        {trade.strike}")

        entry = trade.entry_price
        curr = trade.curr_price
        pnl = trade.pnl_pct

        if entry is not None:
            print(f"Entry Price:   {entry:.2f}")
        if curr is not None:
            print(f"Current Price: {curr:.2f}")
        if pnl is not None:
            color = C.GREEN if pnl >= 0 else C.RED
            print(f"PnL %:         {color}{pnl:.2f}%{C.RESET}")

        print(f"Trail Target:  {trade.trail_target}")
        print(f"Hard SL:       {trade.hard_sl}")
        print(f"Last Update:   {trade.last_update_ms} ms")
        print("=" * 70)

    # ---------------------------------------------------------
    def render_log_panel(self):
        print("\n" + "=" * 70)
        print("📘 TRADE LOG".center(70))
        print("=" * 70)

        if not self.log_buffer:
            print(f"{C.DIM}(No events yet){C.RESET}")
        else:
            for entry in self.log_buffer:
                print(entry)

        print("=" * 70)
