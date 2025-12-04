import datetime as dt
print(">>> Loaded universe.py (Massive-correct expiries + A2-M rules)")
from typing import Optional

# ----------------------------------------------------------
# Symbols
# ----------------------------------------------------------
CORE = ["SPY", "QQQ"]
MATMAN = ["TSLA", "NVDA", "AAPL", "AMZN", "MSFT", "META"]
WEEKLIES = MATMAN[:]  # alias for compatibility

# ----------------------------------------------------------
# A2-M Premium Ceiling Rules
# CORE: Hard $1.00
# MATMAN: Soft $1.50 Mon–Thu, $1.25 Fri
# ----------------------------------------------------------
CORE_CEILING = 1.00
MATMAN_CEILING_MON_THU = 1.50
MATMAN_CEILING_FRI = 1.25


def max_premium(symbol: str) -> float:
    """Returns symbol-specific A2-M premium ceiling."""
    wd = dt.datetime.now().weekday()  # Mon=0..Fri=4

    if symbol in CORE:
        return CORE_CEILING

    if symbol in MATMAN:
        return MATMAN_CEILING_FRI if wd == 4 else MATMAN_CEILING_MON_THU

    return 1.00  # fallback/default


# ----------------------------------------------------------
# A2-M Latency Constraints
# ----------------------------------------------------------
# You can tune symbol-specific latency caps here.
# Typical values for retail IBKR WebSocket + Massive WS:
SYMBOL_LATENCY_CAP_MS = {
    "SPY": 120,
    "QQQ": 120,
    "AAPL": 150,
    "AMZN": 150,
    "META": 150,
    "MSFT": 150,
    "NVDA": 150,
    "TSLA": 180,
}


def max_latency_ms(symbol: str) -> int:
    """Return maximum allowed latency before blocking trade."""
    return SYMBOL_LATENCY_CAP_MS.get(symbol, 150)


# ----------------------------------------------------------
# A2-M Delta-Trail Enablement
# (currently optional; orchestrator checks this)
# ----------------------------------------------------------
def delta_trail_enabled(symbol: str) -> bool:
    """
    In A2-M, delta-aware trailing is optional.
    Enabled only for MATMAN by design (trend-friendly).
    """
    return symbol in MATMAN


# ----------------------------------------------------------
# Trading universe activation
# ----------------------------------------------------------
def get_universe_for_today():
    """
    Very simple:
      • Always trade CORE
      • Add MATMAN/WEEKLIES only on Thu/Fri
    """
    wd = dt.datetime.now().weekday()  # Monday=0
    if wd <= 2:
        return CORE[:]                # Mon–Tue–Wed
    return CORE + WEEKLIES            # Thu–Fri


# ----------------------------------------------------------
# Massive.com expiry rules — REAL listing expiries
# ----------------------------------------------------------
CORE_EXPIRY_WEEKDAYS = {0, 2, 4}  # Mon, Wed, Fri


def get_expiry_for_symbol(symbol: str) -> Optional[str]:
    """
    MASSIVE-CORRECT VERSION:
      • Massive expects the ACTUAL exchange-listed expiry date.

      • CORE (SPY, QQQ):
            - If today is Mon/Wed/Fri → expiry = today
            - Otherwise → next Mon/Wed/Fri

      • WEEKLIES (MATMAN):
            - Mon–Wed → inactive (None)
            - Thu → this Friday
            - Fri → today
    """

    today = dt.date.today()
    wd = today.weekday()

    # CORE
    if symbol in CORE:

        # Same-day expiry?
        if wd in CORE_EXPIRY_WEEKDAYS:
            expiry = today
        else:
            # Find next Mon/Wed/Fri
            for i in range(1, 7):
                cand = today + dt.timedelta(days=i)
                if cand.weekday() in CORE_EXPIRY_WEEKDAYS:
                    expiry = cand
                    break

        return expiry.strftime("%Y-%m-%d")

    # MATMAN/WEEKLIES
    if symbol in WEEKLIES:

        # Mon–Tue–Wed → no weekly expiry trades
        if wd <= 2:
            return None

        if wd == 3:  # Thursday → Friday weekly
            expiry = today + dt.timedelta(days=1)
        else:        # Friday → today
            expiry = today

        return expiry.strftime("%Y-%m-%d")

    return None
