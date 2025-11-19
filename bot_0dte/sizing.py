"""
Notional-based sizing for 0DTE directional trades.

Your model:
    • 7% notional per trade
    • 50% stop-loss = 3.5% worst-case account risk
    • sizing is based on underlying notional (price * 100)

API:
    size_from_equity(nlv: float, underlying_price: float) -> int
"""


# ============================================================
# CONFIG
# ============================================================

RISK_NOTIONAL_FRACTION = 0.07    # 7% notional exposure
MIN_CONTRACTS = 1
MAX_CONTRACTS = 10


# ============================================================
# MAIN API
# ============================================================

def size_from_equity(nlv: float, underlying_price: float) -> int:
    """
    Notional-based position sizing.

    Formula:
        max_notional = nlv * RISK_NOTIONAL_FRACTION
        contract_notional = underlying_price * 100
        qty = floor(max_notional / contract_notional)

    Ensures:
        • Position scales with underlying
        • Risk is proportional to account size
        • Actual downside risk = 3.5% due to 50% SL
    """

    if nlv <= 0 or underlying_price <= 0:
        return MIN_CONTRACTS

    max_notional = nlv * RISK_NOTIONAL_FRACTION
    contract_notional = underlying_price * 100

    raw_qty = max_notional / contract_notional
    qty = int(max(MIN_CONTRACTS, min(MAX_CONTRACTS, raw_qty)))

    return qty

