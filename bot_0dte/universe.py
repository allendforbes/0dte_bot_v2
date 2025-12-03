import datetime as dt
print(">>> Loaded universe.py (Massive-correct expiries)")
from typing import Optional

# ----------------------------------------------------------
# Symbols
# ----------------------------------------------------------
CORE = ["SPY", "QQQ"]
WEEKLIES = ["TSLA", "NVDA", "AAPL", "AMZN", "MSFT", "META"]

def get_universe_for_today():
    """
    Very simple:
      • Always trade CORE
      • Add WEEKLIES only on Thu/Fri
    """
    wd = dt.datetime.now().weekday()  # Monday=0
    if wd <= 2:
        return CORE[:]                # Mon–Tue–Wed
    return CORE + WEEKLIES            # Thu–Fri


# ----------------------------------------------------------
# Massive.com expiry rules — REAL listing expirations (NOT OCC +1)
# ----------------------------------------------------------
# CORE expiries happen every:
#   Monday (0), Wednesday (2), Friday (4)
CORE_EXPIRY_WEEKDAYS = {0, 2, 4}

def get_expiry_for_symbol(symbol: str) -> Optional[str]:
    """
    MASSIVE-CORRECT VERSION (final):
      • Massive expects the ACTUAL exchange-listed expiry date, NOT
        the OCC settlement (+1) date.

      • CORE (SPY, QQQ):
            - If today is Mon/Wed/Fri → expiry = today
            - Otherwise → next Mon/Wed/Fri

      • WEEKLIES:
            - Mon–Wed → inactive (returns None)
            - Thu → this Friday
            - Fri → today
    """

    today = dt.date.today()
    wd = today.weekday()

    # -----------------------------------------------------
    # CORE SYMBOLS
    # -----------------------------------------------------
    if symbol in CORE:

        # Same-day expiry available?
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

    # -----------------------------------------------------
    # WEEKLIES — valid only Thu/Fri
    # -----------------------------------------------------
    if symbol in WEEKLIES:

        # Mon–Tue–Wed → no weekly expiry trades
        if wd <= 2:
            return None

        if wd == 3:  # Thursday → Friday weekly
            expiry = today + dt.timedelta(days=1)
        else:        # Friday → today
            expiry = today

        return expiry.strftime("%Y-%m-%d")

    # Unknown symbol
    return None
