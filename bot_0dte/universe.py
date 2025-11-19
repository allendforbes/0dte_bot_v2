import datetime as dt


CORE = ["SPY", "QQQ"]

WEEKLIES = ["TSLA", "NVDA", "AAPL", "AMZN", "MSFT", "META"]


def get_universe_for_today():
    """
    Universe rules:
        • Mon–Wed → CORE only
        • Thu–Fri → CORE + WEEKLIES

    WEEKLY names are included Thu–Fri, but expiry logic determines
    whether they trade 1DTE (Thu) or 0DTE (Fri).
    """
    wd = dt.datetime.now().weekday()  # Mon=0, Tue=1, ..., Fri=4

    if wd <= 2:  # Mon-Tue-Wed
        return CORE.copy()

    return CORE + WEEKLIES


def get_expiry_for_symbol(symbol: str) -> str:
    """
    Core expiry logic:
        SPY/QQQ:
            • Always return today (0DTE)

        WEEKLIES:
            • Mon–Wed → return None (skip trading)
            • Thu    → return Friday (1DTE)
            • Fri    → return today (0DTE)
    """

    now = dt.datetime.now()
    wd = now.weekday()

    today = now.strftime("%Y-%m-%d")

    # ----------------------------------------------------------
    # 1) CORE names — strict 0DTE every day
    # ----------------------------------------------------------
    if symbol in CORE:
        return today

    # ----------------------------------------------------------
    # 2) WEEKLIES — only Thu & Fri
    # ----------------------------------------------------------
    if symbol in WEEKLIES:
        # Mon–Wed → skip
        if wd <= 2:
            return None

        # Thu → return Friday (1DTE)
        if wd == 3:
            friday = now + dt.timedelta(days=1)
            return friday.strftime("%Y-%m-%d")

        # Fri → today 0DTE
        if wd == 4:
            return today

    return None


