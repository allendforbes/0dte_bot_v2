# ======================================================================
#  FORCE PROJECT ROOT INTO PYTHONPATH — MUST BE FIRST
# ======================================================================
import sys, os, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
print("PYTHON ROOT PATH:", ROOT)
print("bot_0dte exists:", (ROOT / "bot_0dte").exists())

# ======================================================================
#  IMPORTS
# ======================================================================
import asyncio
import time
from pprint import pprint

from bot_0dte.data.adapters.ib_underlying_adapter import IBUnderlyingAdapter
from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.data.providers.massive.massive_mux import MassiveMux
from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.universe import get_expiry_for_symbol


SYMS = ["SPY", "QQQ"]


async def main():
    print("\n============================")
    print("  MARKET PIPELINE DIAGNOSTIC")
    print("============================\n")

    chain = ChainAggregator(SYMS)

    # ---------------------------------------------------------
    # STEP 1 — IBKR Underlying
    # ---------------------------------------------------------
    print("[STEP 1] Connecting IBKR underlying...")
    ib = IBUnderlyingAdapter(host="127.0.0.1", port=4002, client_id=88)
    await ib.connect()
    await ib.subscribe(SYMS)

    await asyncio.sleep(0.5)
    print("[OK] Underlying subscription sent\n")

    # ---------------------------------------------------------
    # STEP 2 — MASSIVE Options WS + Mux
    # ---------------------------------------------------------
    opts = MassiveOptionsWSAdapter.from_env()
    mux = MassiveMux(options_ws=opts, ib_underlying=ib)

    # Chain aggregator updates on NBBO
    async def _diag_underlying(ev):
        print("[UNDERLYING]", ev)

    async def _diag_option(ev):
        chain.update_from_nbbo(ev)

    mux.on_underlying(_diag_underlying)
    mux.on_option(_diag_option)

    exp_map = {s: get_expiry_for_symbol(s) for s in SYMS}

    print("[STEP 2] Connecting MassiveMux...")
    await mux.connect(SYMS, exp_map)

    print("[WAIT] Listening 5 seconds for NBBO + underlying + chain hydration...\n")
    await asyncio.sleep(5)

    # ---------------------------------------------------------
    # RESULTS
    # ---------------------------------------------------------
    print("\n============================")
    print("  RESULTS")
    print("============================")

    for s in SYMS:
        snap = chain.get(s)
        print(f"\n--- {s} snapshot ---")
        if snap:
            print(f"Rows: {len(snap.rows)}  Last TS: {snap.last_update_ts_ms}")
            pprint(snap.rows[:5])
        else:
            print("NO DATA RECEIVED")

    print("\nIf underlying ticks are missing → IBKR issue.")
    print("If OCC rows empty → ContractEngine or Mux routing issue.")
    print("If NBBO absent → Massive subscription issue.\n")


if __name__ == "__main__":
    asyncio.run(main())
