#!/usr/bin/env python3
"""
Stress Test — Hybrid Market Data Pipeline
-----------------------------------------
Validates the full route:

IBKR Underlying →
MassiveMux →
MassiveContractEngine OCC refresh →
Massive NBBO →
ChainAggregator

This script intentionally overloads the system and prints:
    • Underlying tick rate
    • NBBO tick rate
    • Chain hydration (rows)
    • Engine refresh events
    • WS subscription health
"""
import sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
print("PYTHON ROOT PATH:", ROOT)
sys.path.insert(0, ROOT)

# validate that bot_0dte is visible
print("bot_0dte exists:", os.path.isdir(os.path.join(ROOT, "bot_0dte")))

import asyncio
import time
import os
from pprint import pprint

from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.data.providers.massive.massive_mux import MassiveMux
from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.universe import get_expiry_for_symbol


SYMS = ["SPY", "QQQ"]


async def main():

    print("\n==============================")
    print("  STRESS TEST: MARKET PIPELINE")
    print("==============================\n")

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------
    stats = {
        "underlying": {s: 0 for s in SYMS},
        "nbbo": {s: 0 for s in SYMS},
        "engine_refresh": {s: 0 for s in SYMS},
    }

    # ------------------------------------------------------------------
    # Chain aggregator
    # ------------------------------------------------------------------
    chain = ChainAggregator(SYMS)


    # ------------------------------------------------------------------
    # IBKR Underlying
    # ------------------------------------------------------------------
    print("[STEP] Connecting IBKR...")
    ib = IBUnderlyingAdapter(host="127.0.0.1", port=4002, client_id=97)
    await ib.connect()
    await ib.subscribe(SYMS)

    print("[OK] IBKR connected.\n")

    # ------------------------------------------------------------------
    # Massive WebSocket
    # ------------------------------------------------------------------
    opts = MassiveOptionsWSAdapter.from_env()

    # ------------------------------------------------------------------
    # Attach NBBO handler
    # ------------------------------------------------------------------
    def on_nbbo(ev):
        root = ev["symbol"]
        stats["nbbo"][root] += 1
        chain.update_from_nbbo(ev)

    opts.on_option(on_nbbo)

    # ------------------------------------------------------------------
    # MUX (connects underlying + NBBO + engines)
    # ------------------------------------------------------------------
    mux = MassiveMux(options_ws=opts, ib_underlying=ib)

    # Count engine refresh events
    async def engine_watch(event, sym):
        stats["engine_refresh"][sym] += 1

    # Patch engines after mux.connect()
    async def patch_engines():
        for sym, eng in mux.engines.items():
            orig = eng._refresh

            async def wrapped(price, _eng=eng, _sym=sym):
                stats["engine_refresh"][_sym] += 1
                return await orig(price)

            eng._refresh = wrapped

    # Underlying callback
    async def on_underlying(ev):
        sym = ev["symbol"]
        stats["underlying"][sym] += 1

    mux.on_underlying(on_underlying)

    # Expiry map
    exp_map = {s: get_expiry_for_symbol(s) for s in SYMS}

    print("[STEP] Connecting MassiveMux...")
    await mux.connect(SYMS, exp_map)
    await asyncio.sleep(0.5)
    await patch_engines()

    print("[OK] Mux + Massive connected.\n")

    # ------------------------------------------------------------------
    # MAIN STRESS LOOP — 5 seconds
    # ------------------------------------------------------------------
    print("[TEST] Running 5-second stress test...\n")
    start = time.time()

    while time.time() - start < 5:
        await asyncio.sleep(0.20)

        # Clear screen
        os.system("clear")

        print("=== MARKET PIPELINE STRESS TEST ===")
        print(f"Runtime: {time.time() - start:0.2f} sec\n")

        # Underlying stats
        print("--- Underlying Tick Rates ---")
        for s in SYMS:
            print(f"{s}: {stats['underlying'][s]} ticks")

        # NBBO stats
        print("\n--- NBBO Tick Rates ---")
        for s in SYMS:
            print(f"{s}: {stats['nbbo'][s]} ticks")

        # Engine refresh counts
        print("\n--- ContractEngine Refresh Events ---")
        for s in SYMS:
            print(f"{s}: {stats['engine_refresh'][s]} refresh calls")

        # Chain hydration
        print("\n--- Chain Snapshots ---")
        for s in SYMS:
            snap = chain.get(s)
            if not snap:
                print(f"{s}: NO DATA")
                continue
            print(f"{s}: rows={len(snap.rows)} last_ts={snap.last_update_ts_ms}")
            sample = snap.rows[:3]
            pprint(sample)

        print("\n====================================================")
        print("If NBBO or Underlying ticks = 0 → subscription issue.")
        print("If rows remain empty → ChainAggregator hydration issue.")
        print("If refresh=0 with moving price → ContractEngine not triggered.")
        print("====================================================\n")

    # ------------------------------------------------------------------
    # DONE
    # ------------------------------------------------------------------
    print("\n=== FINAL RESULTS ===")
    pprint(stats)
    print("\nDone.\n")

    # === CLEAN SHUTDOWN ===
    await mux.close()

    # IBKR shutdown
    try:
        close_fn = getattr(ib, "close", None)
        if close_fn and asyncio.iscoroutinefunction(close_fn):
            await close_fn()
        else:
            ib.ib.disconnect()
    except Exception:
        pass

