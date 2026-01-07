def size_from_premium(
    equity: float,
    option_price: float,
    exposure_pct: float,
    stop_pct: float,
    multiplier: int = 100,
) -> int:
    """
    Premium-based fixed-risk sizing.

    Guarantees:
        max_loss <= equity * exposure_pct * stop_pct
    """

    if equity <= 0 or option_price <= 0:
        return 0

    max_exposure = equity * exposure_pct
    contracts = int(max_exposure // (option_price * multiplier))

    while contracts > 0:
        max_loss = contracts * option_price * multiplier * stop_pct
        if max_loss <= equity * exposure_pct * stop_pct:
            return contracts
        contracts -= 1

    return 0
