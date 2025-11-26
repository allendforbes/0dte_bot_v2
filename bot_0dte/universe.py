import datetime as dt
print(">>> Loaded universe.py (corrected OCC logic)")
from typing import Optional

# ----------------------------------------------------------
# Symbol groups
# ----------------------------------------------------------

# CORE symbols (daily expirations: SPY/QQQ)
CORE = ["SPY", "QQQ"]

# Weeklies trade-only symbols
WEEKLIES = ["TSLA", "NVDA", "AAPL", "AMZN", "MSFT", "META"]

# SPY/QQQ weekly expiries occur: Monday, Wednesday, Friday
CORE_EXPIRY_WEEKDAYS = {0, 2, 4}  # Mon=0, Wed=2, Fri=4


# ----------------------------------------------------------
# Universe selection
# ----------------------------------------------------------

def get_universe_for_today():
    """
    Universe rules:
        • Mon–Wed → CORE only
        • Thu–Fri → CORE + WEEKLIES
        
    WEEKLIES only trade Thu/Fri (expiry logic decides 1DTE vs 0DTE)
    """
    wd = dt.datetime.now().weekday()  # Mon=0 ... Sun=6

    if wd <= 2:     # Mon,Tue,Wed
        return CORE.copy()

    return CORE + WEEKLIES


# ----------------------------------------------------------
# Helpers
# ----------------------------------------------------------

def _next_expiry(start: dt.date, weekday_set):
    """
    OCC daily options expire 12:01 AM ET *on* their expiration date.

    Meaning:
        - A Wednesday expiry is already expired by Wednesday's open.
        - On Mon/Wed/Fri we *cannot* use today's date as the trading expiry.
        - Instead, we must jump to the *next* Mon/Wed/Fri.

    Correct behavior:
        - If today is Wed, next expiry = Fri
        - If today is Fri, next expiry = Mon
        - If today is Mon, next expiry = Wed
    """

    today = start
    wd = today.weekday()

    # If today is an expiry day, skip it (it expired at 00:01am)
    if wd in weekday_set:
        for i in range(1, 7):
            cand = today + dt.timedelta(days=i)
            if cand.weekday() in weekday_set:
                return cand

    # Otherwise, find first occurrence >= today
    for i in range(7):
        cand = today + dt.timedelta(days=i)
        if cand.weekday() in weekday_set:
            return cand

    # Fallback (never hit)
    return today


def _occ_roll(trading_expiry: dt.date):
    """
    OCC uses next-calendar-day expiration at 12:01 AM.

    Example:
        Trading expiry  = Friday (regular market convention)
        OCC expiry date = Saturday (Fri + 1)
    """
    return trading_expiry + dt.timedelta(days=1)


# ----------------------------------------------------------
# Expiry logic (core)
# ----------------------------------------------------------

def get_expiry_for_symbol(symbol: str) -> Optional[str]:
    """
    Final unified expiry logic (Massive-safe, OCC-correct):

    CORE (SPY/QQQ: daily):
        • Trade the *next* Mon/Wed/Fri (not today's!)
        • OCC expiry = trading_expiry + 1 day

    WEEKLIES:
        • Mon–Wed: do not trade (return None)
        • Thu: trade Friday expiry
        • Fri: trade Friday expiry
        • OCC expiry = trading_expiry + 1 day

    Returns:
        OCC expiry date "YYYY-MM-DD"
        or None if symbol is inactive today.
    """

    now = dt.datetime.now()
    today = now.date()
    wd = today.weekday()

    # ----------------------------------------
    # CORE 0DTE (SPY / QQQ)
    # ----------------------------------------
    if symbol in CORE:
        trading_expiry = _next_expiry(today, CORE_EXPIRY_WEEKDAYS)
        occ_expiry = _occ_roll(trading_expiry)
        return occ_expiry.strftime("%Y-%m-%d")

    # ----------------------------------------
    # WEEKLIES (TSLA, NVDA, etc)
    # ----------------------------------------
    if symbol in WEEKLIES:

        # Mon–Wed → no weekly 0DTE/1DTE trading
        if wd <= 2:
            return None

        # Thu → trade Friday weekly
        if wd == 3:
            trading_expiry = today + dt.timedelta(days=1)
        else:
            # Fri → trade Friday weekly
            trading_expiry = today

        occ_expiry = _occ_roll(trading_expiry)
        return occ_expiry.strftime("%Y-%m-%d")

    # Unknown symbol
    return None
