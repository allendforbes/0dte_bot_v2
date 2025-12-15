import sys, os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

print("\nPYTHON ROOT PATH:", ROOT)
print("bot_0dte exists:", os.path.isdir(os.path.join(ROOT, "bot_0dte")))

import asyncio
import time

from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.data.providers.massive.massive_mux import MassiveMux
from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter

from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.strategy.elite_entry_diagnostic import EliteEntryEngine
from bot_0dte.chain.chain_freshness_v2 import ChainFreshnessV2
from bot_0dte.chain.greek_injector import GreekInjector
from bot_0dte.data.providers.massive.massive_rest_snapshot_client import MassiveSnapshotClient

from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol

API_KEY = "<YOUR API KEY HERE>"  # optional if MASSIVE_API_KEY env var already set


async def main():
    # ---------------------------------------------------------
    # COMPONENTS
    # ---------------------------------------------------------
    symbols = get_universe_for_today()
    expiry_map = {s: get_expiry_for_symbol(s) for s in symbols}

    print("\n=== Hydration MVP Diagnostic ===")
    print("Universe:", symbols, "\n")

    ws = MassiveOptionsWSAdapter.from_env()
    ib = IBUnderlyingAdapter(host="127.0.0.1", port=4002, client_id=33)

    mux = MassiveMux(options_ws=ws, ib_underlying=ib)

    agg = ChainAggregator(symbols)
    freshness = {s: ChainFreshnessV2() for s in symbols}

    snap_client = MassiveSnapshotClient(API_KEY)
    injector = GreekInjector(snap_client)

    # ---------------------------------------------------------
    # CALLBACKS
    # ---------------------------------------------------------
    async def on_option(event):
        # 1. Hydrate NBBO event with REST
        enriched = await injector.enrich(event)

        if not enriched.get("_hydrated"):
            print("UNHYDRATED:", enriched.get("contract"))
            return

        freshness[ enriched["symbol"] ].update_frame()
        agg.update_from_nbbo(enriched)

    async def on_underlying(event):
        sym = event["symbol"]
        freshness[sym].update_heartbeat()

    mux.on_option(on_option)
    mux.on_underlying(on_underlying)

    # ---------------------------------------------------------
    print("\nConnecting IBKR...")
    await ib.connect()
    await ib.subscribe(symbols)
    await asyncio.sleep(1.0)

    print("\nConnecting MassiveMux...")
    await mux.connect(symbols, expiry_map)

    # ---------------------------------------------------------
    print("\nRunning hydration test for 10 seconds...\n")
    start = time.time()

    while time.time() - start < 10:
        for sym in symbols:
            snap = agg.get_chain(sym)
            print(
                f"{sym}: rows={len(snap)} | "
                f"hydrated={'yes' if len(snap)>0 else 'NO'} | "
                f"frame_age={freshness[sym].frame_age_ms():.0f}ms"
            )

        print("-" * 60)
        await asyncio.sleep(1)

    print("\n=== DONE ===")
    await mux.close()
    ib.ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
