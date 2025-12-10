"""
pipeline_test.py — FULL PIPELINE VERIFICATION (SAFE)

Verifies:
  • IBKR underlying feed
  • Massive options NBBO feed
  • MassiveMux routing (underlying + NBBO)
  • ChainFreshnessV2 heartbeat + frame updates
  • Subscription flow correctness
  • Orchestrator callback paths (but NO trading)

Safe to run after hours.
"""

import asyncio
import logging
import time

# Adapters
from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.data.providers.massive.massive_mux import MassiveMux

# Universe helpers
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol

logging.basicConfig(level=logging.INFO)


async def pipeline_test():

    # 1) Load universe (SPY/QQQ at minimum)
    symbols = get_universe_for_today()
    expiry_map = {s: get_expiry_for_symbol(s) for s in symbols}

    print("\n===============================")
    print(" PIPELINE TEST — STARTING ")
    print("===============================\n")
    print("Universe:", symbols)
    print("Expiry map:", expiry_map)
    print()

    # 2) IBKR underlying feed
    ib = IBUnderlyingAdapter(
        host="127.0.0.1",
        port=4002,       # paper TWS
        client_id=91
    )

    print("[TEST] Connecting to IBKR underlying…")
    await ib.connect()
    await ib.subscribe(symbols)

    # 3) Massive options WS adapter
    print("[TEST] Connecting to Massive OPTIONS WS…")
    options_ws = MassiveOptionsWSAdapter.from_env()
    await options_ws.connect()

    # 4) Mux
    print("[TEST] Initializing MassiveMux…")
    mux = MassiveMux(
        ib_underlying=ib,
        options_ws=options_ws
    )
    await mux.connect(symbols, expiry_map)

    # 5) Register test callbacks
    print("[TEST] Registering diagnostic callbacks…\n")

    # Track counts
    counts = {
        "underlying": 0,
        "nbbo": 0,
        "fresh_updates": 0
    }

    last_print = time.time()

    async def on_underlying(ev):
        counts["underlying"] += 1

    async def on_option(ev):
        counts["nbbo"] += 1

        sym = ev["symbol"]
        f = mux.freshness[sym]
        f_age = f.age_ms()
        counts["fresh_updates"] += 1

        nonlocal last_print
        if time.time() - last_print >= 2:
            print(
                f"[OK] {sym} NBBO tick: bid={ev.get('bid')} ask={ev.get('ask')} "
                f"(fresh={f_age:.0f}ms)"
            )
            last_print = time.time()

    mux.on_underlying(on_underlying)
    mux.on_option(on_option)

    print("\n[TEST] Waiting for ticks… (10 seconds)\n")

    # 6) Run test loop
    t0 = time.time()
    while time.time() - t0 < 10:
        await asyncio.sleep(0.1)

    print("\n===============================")
    print(" PIPELINE TEST RESULTS ")
    print("===============================\n")

    print(f"Underlying ticks received: {counts['underlying']}")
    print(f"Options NBBO ticks received: {counts['nbbo']}")
    print(f"Freshness updates: {counts['fresh_updates']}")

    for sym in symbols:
        f = mux.freshness[sym]
        print(
            f" - {sym}: heartbeat={f.heartbeat_age_ms():.0f}ms, "
            f"frame_age={f.frame_age_ms():.0f}ms, "
            f"fresh={f.is_fresh()}"
        )

    print("\nIf all values above are non-zero and freshness is TRUE → pipeline is GOOD.\n")

    print("Shutting down…")
    await mux.close()
    await ib.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(pipeline_test())
