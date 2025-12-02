import datetime as dt
print(">>> Loaded universe.py (Massive-correct expiries)")
from typing import Optional

# Symbols
CORE = ["SPY", "QQQ"]
WEEKLIES = ["TSLA", "NVDA", "AAPL", "AMZN", "MSFT", "META"]

def get_universe_for_today():
    """
    Simpler:
      - Always trade CORE
      - Trade WEEKLIES Thu/Fri
    """
    wd = dt.datetime.now().weekday()
    if wd <= 2:
        return CORE[:]       # Mon-Tue-Wed
    return CORE + WEEKLIES   # Thu-Fri


# ----------------------------------------------------------
# Massive.com expiry rules (NO OCC roll)
# ----------------------------------------------------------
CORE_EXPIRY_WEEKDAYS = {0, 2, 4}   # Mon, Wed, Fri

def get_expiry_for_symbol(symbol: str) -> Optional[str]:
    """
    MASSIVE-CORRECT VERSION:
      • CORE (SPY / QQQ):
            - If today is Mon/Wed/Fri → expiry = today
            - Otherwise → next upcoming Mon/Wed/Fri

      • WEEKLIES:
            - Thu: expiry = Friday of this week
            - Fri: expiry = today
            - Mon–Wed → inactive

    NOTE:
        Massive EXPECTS the REAL market expiration date,
        NOT the OCC settlement date (+1 day).
    """
    today = dt.date.today()
    wd = today.weekday()

    # ----------------------------------------
    # CORE (SPY / QQQ)
    # ----------------------------------------
    if symbol in CORE:

        if wd in CORE_EXPIRY_WEEKDAYS:
            expiry = today
        else:
            # find next Mon/Wed/Fri
            for i in range(1, 7):
                cand = today + dt.timedelta(days=i)
                if cand.weekday() in CORE_EXPIRY_WEEKDAYS:
                    expiry = cand
                    break

        return expiry.strftime("%Y-%m-%d")

    # ----------------------------------------
    # WEEKLIES (Thu/Fri only)
    # ----------------------------------------
    if symbol in WEEKLIES:

        if wd <= 2:
            return None     # Mon–Wed no weekly trading

        if wd == 3:
            # Thursday → Friday weekly
            expiry = today + dt.timedelta(days=1)
        else:
            # Friday → today
            expiry = today

        return expiry.strftime("%Y-%m-%d")

    return None
