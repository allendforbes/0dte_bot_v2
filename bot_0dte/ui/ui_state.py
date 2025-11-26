class TradeState:
    """Represents live state of the current trade for UI purposes only."""

    def __init__(self):
        self.active = False
        self.symbol = None
        self.contract = None
        self.bias = None
        self.regime = None
        self.grade = None
        self.entry_price = None
        self.curr_price = None
        self.pnl_pct = None
        self.trail_target = None
        self.hard_sl = None
        self.last_update_ms = None

    def reset(self):
        self.__init__()


class UIState:
    """Global dashboard-friendly state container."""

    def __init__(self):
        # Per symbol underlying panel
        self.underlying = {}  # {symbol: {"price": .., "bid": .., "ask": .., "signal": .., ...}}

        # Active trade status
        self.trade = TradeState()

        # Lifecycle log (UI panel 3)
        self.logs = []

    # --------------- Update helpers ---------------

    def update_underlying(self, symbol, price, bid, ask, signal=None, strike=None):
        self.underlying[symbol] = {
            "price": price,
            "bid": bid,
            "ask": ask,
            "signal": signal,
            "strike": strike,
        }

    def log(self, msg):
        # Keep last 50 messages
        self.logs.append(msg)
        self.logs = self.logs[-50:]
