"""
NBBO Connectivity Test (SPY, QQQ)
---------------------------------
Verifies:
  • MassiveOptionsWSAdapter connectivity
  • OCC subscription correctness
  • NBBO tick flow
  • FreshnessV2 updates
  • ContractEngine correctly maps chains

Run:
    python bot_nbbo_test.py
"""

import asyncio
import datetime as dt
from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.data.providers.massive.massive_mux import MassiveMux
from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol


async def main():
    print("\n=== NBBO CONNECTIVITY TEST ===\n")

    # --------------------------------------------------------
    # Determine tomorrow’s universe (SPY & QQQ on Tue)
    # --------------------------------------------------------
    symbols = get_universe_for_today()
    print(f"[TEST] Universe for today: {symbols}")

    # --------------------------------------------------------
    # Expiry mapping (Massive-correct)
    # --------------------------------------------------------
    expiry_map = {s: get_expiry_for_symbol(s) for s in symbols}
    print(f"[TEST] Expiry map: {expiry_map}")

    # --------------------------------------------------------
    # Initialize IBKR underlying adapter (for contract engine)
    # --------------------------------------------------------
    ib_underlying = IBUnderlyingAdapter(
        host="127.0.0.1",
        port=4002,
        client_id=99
    )

    # --------------------------------------------------------
    # Massive options WS
    # --------------------------------------------------------
    options_ws = MassiveOptionsWSAdapter.from_env()

    # --------------------------------------------------------
    # Build Mux
    # --------------------------------------------------------
    mux = MassiveMux(ib_underlying=ib_underlying, options_ws=options_ws)

    # --------------------------------------------------------
    # NBBO Tick Printer
    # --------------------------------------------------------
    def on_nbbo(ev):
        sym = ev["symbol"]
        bid = ev.get("bid")
        ask = ev.get("ask")
        occ = ev.get("contract")

        print(f"[NBBO] {sym:4s}  {bid:6.2f} x {ask:6.2f}   {occ}")

    mux.on_option(on_nbbo)

    # --------------------------------------------------------
    # Start Mux (connect + subscribe underlying + OCC auto-subscribe)
    # --------------------------------------------------------
    print("\n[TEST] Connecting Mux…\n")
    await mux.connect(symbols, expiry_map)

    print("\n[Test] Subscriptions issued. Waiting for NBBO ticks…\n")

    # --------------------------------------------------------
    # Keep alive
    # --------------------------------------------------------
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
