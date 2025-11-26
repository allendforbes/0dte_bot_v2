import asyncio
from bot_0dte.bot_start import build_orchestrator_for_sim

async def main():
    orch = await build_orchestrator_for_sim(symbols=["SPY"])

    # ------------------------------------------------------
    # REPLAY TICKS — Replace these with real chart points
    # Format: (timestamp, price, bid, ask)
    # ------------------------------------------------------
    ticks = [
        # (time, price, bid, ask)
        ("11:20:01", 680.10, 680.09, 680.11),
        ("11:20:05", 680.35, 680.33, 680.36),
        ("11:20:12", 680.60, 680.58, 680.62),
        ("11:20:22", 681.00, 680.98, 681.02),
        ("11:20:42", 681.40, 681.38, 681.41),
        ("11:21:05", 681.75, 681.73, 681.77),
        ("11:21:28", 682.10, 682.08, 682.12),  # ← signal will fire here
        ("11:22:00", 682.55, 682.53, 682.57),  # trade progresses
        ("11:22:30", 682.80, 682.78, 682.82),
        ("11:23:05", 683.15, 683.13, 683.17),  # likely trail bump
        ("11:23:22", 682.60, 682.57, 682.62),  # pullback
        ("11:23:45", 681.95, 681.93, 681.96),  # trail exit will likely hit
    ]

    # ------------------------------------------------------
    # FEED INTO ORCHESTRATOR
    # ------------------------------------------------------
    for ts, price, bid, ask in ticks:
        event = {
            "symbol": "SPY",
            "price": price,
            "bid": bid,
            "ask": ask,
            "_recv_ts": 0.0,
        }
        await orch._on_underlying(event)
        await asyncio.sleep(0.05)  # small time delay so UI renders

    print("\nReplay complete.\n")

asyncio.run(main())
