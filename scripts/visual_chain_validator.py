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
from prettytable import PrettyTable

from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol


async def main():

    syms = get_universe_for_today()
    chain = ChainAggregator(syms)

    ws = MassiveOptionsWSAdapter.from_env()
    ws.on_option(lambda ev: chain.update_from_nbbo(ev))

    exp_map = {s: get_expiry_for_symbol(s) for s in syms}

    # Dummy mux (REST-only validator)
    class DummyMux:
        freshness = {}
        engines = {}

    ws.parent_orchestrator = DummyMux()

    # ---------------------------------------------------------
    # Subscribe to a WIDE OCC RANGE for diagnostics
    # ---------------------------------------------------------
    topics = []
    for s in syms:
        exp = exp_map[s]
        if not exp:
            continue

        datecode = exp[2:].replace("-", "")

        for strike in range(400, 800):  # broad strike sweep
            for side in ("C", "P"):
                occ = f"{s}{datecode}{side}{strike*1000:08d}"
                topics.append(f"Q.O:{occ}")

    ws.set_occ_subscriptions(topics)
    await ws.connect()

    # ---------------------------------------------------------
    # Live table display
    # ---------------------------------------------------------
    while True:
        os.system("clear")
        table = PrettyTable()
        table.field_names = ["SYM", "Strike", "Right", "Bid", "Ask", "IV", "Gamma", "Vol", "TS"]

        for s in syms:
            snap = chain.get(s)
            if not snap:
                continue

            for r in snap.rows[:20]:
                table.add_row([
                    s, r["strike"], r["right"],
                    r["bid"], r["ask"],
                    r.get("iv"), r.get("gamma"), r.get("volume"),
                    time.strftime('%H:%M:%S', time.localtime(r["_recv_ts"]))
                ])

        print(table)
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
