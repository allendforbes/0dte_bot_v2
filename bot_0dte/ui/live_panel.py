import time
import shutil
import datetime as dt


# Minimal ANSI safely wrapped
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


class LivePanel:
    """
    Lightweight ASCII market panel updated on every tick.
    Non-blocking, async-friendly (no sleeps).
    """

    def __init__(self):
        self.last_render = 0
        self.min_interval = 0.25  # Throttle rendering
        self.rows = {}
        print("[UI] LivePanel ready.")

    def set_status(self, msg: str):
        """Print a lightweight pipeline status message."""
        print(f"[STATUS] {msg}")

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
        """
        Store latest row and render if interval passed.
        """

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

    # ---------------------------------------------------------
    def _fmt_price(self, p):
        if p is None:
            return f"{C.DIM}--{C.RESET}"
        return f"{C.BOLD}{p:.2f}{C.RESET}"

    # ---------------------------------------------------------
    def _fmt_signal(self, sig):
        if sig is None:
            return f"{C.DIM}--{C.RESET}"

        bias = sig["bias"]
        color = C.GREEN if bias == "CALL" else C.RED
        grade = sig.get("grade", "?")
        regime = sig.get("regime", "?")

        return f"{color}{bias}{C.RESET} ({grade}, {regime})"

    # ---------------------------------------------------------
    def _fmt_strike(self, st):
        if st is None:
            return f"{C.DIM}--{C.RESET}"

        return f"{C.CYAN}{st['strike']}{st['right']}{C.RESET} @ {st['premium']:.2f}"

    # ---------------------------------------------------------
    def render(self):
        term_width = shutil.get_terminal_size((120, 30)).columns

        print("\033[2J\033[H", end="")  # Clear + home

        print("=" * term_width)
        print("📡  LIVE MARKET PANEL".center(term_width))
        print("=" * term_width)
        print(
            f"{'Symbol':<8} {'Price':<10} {'Bid':<10} {'Ask':<10} {'Signal':<25} {'Strike':<20}"
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
        now = dt.datetime.now().strftime("%H:%M:%S")
        print(f"{C.DIM}Updated {now}{C.RESET}")
