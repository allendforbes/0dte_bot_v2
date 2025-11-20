import asyncio
import time
from pprint import pprint

from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


# ======================================================================
#  SYNTHETIC OPTION CHAIN (ATM ±1, premium near $1)
# ======================================================================
def synthetic_chain(symbol: str, underlying: float):
    atm = round(underlying)
    strikes = [atm - 1, atm, atm + 1]

    chain = []
    for k in strikes:
        mid = 1.00  # guarantee convex strike selection hit
        chain.append(
            {
                "symbol": symbol,
                "expiry": time.strftime("%Y%m%d"),
                "strike": k,
                "right": "C",
                "bid": mid - 0.05,
                "ask": mid + 0.05,
                "last": mid,
            }
        )
        chain.append(
            {
                "symbol": symbol,
                "expiry": time.strftime("%Y%m%d"),
                "strike": k,
                "right": "P",
                "bid": mid - 0.05,
                "ask": mid + 0.05,
                "last": mid,
            }
        )
    return chain


# ======================================================================
#  MOCK ENGINE — matches ExecutionEngine interface, no IBKR required
# ======================================================================
class MockEngine:
    def __init__(self):
        class _Acct:
            def __init__(self):
                self.net_liq = 100_000
                self.updated = time.time()

            def is_fresh(self):
                return True

        self.account_state = _Acct()
        self.expiry_map = {}
        self.last_price = {}
        self.md_chain_cache = {}

    async def start(self):
        print("[ENGINE] Mock engine started.")

    async def send_bracket(self, **req):
        print("\n================= MOCK EXECUTION =================")
        pprint(req)
        print("===================================================")
        return {"ok": True, "req": req}


# ======================================================================
#  BUILD ORCHESTRATOR (injected mock engine + synthetic feed)
# ======================================================================
def build_orchestrator(symbols):
    engine = MockEngine()

    orch = Orchestrator(
        engine=engine,
        chain_bridge=None,  # Not used in sim — selector reads md_chain_cache
        feed=None,  # We manually call on_market_data
        telemetry=Telemetry(),
        logger=StructuredLogger(),
        universe=symbols,
    )

    # Inject mock caches back into orchestrator's engine
    engine.expiry_map = orch.expiry_map
    engine.last_price = orch.last_price
    engine.md_chain_cache = orch.md_chain_cache

    return orch


# ======================================================================
#  SYNTHETIC TICK (A+ CALL every time)
# ======================================================================
def make_tick(symbol, px):
    vwap = px + 0.50
    return {
        "symbol": symbol,
        "price": px,
        "bid": 0.95,
        "ask": 1.05,
        "vwap": vwap,
        "vwap_dev_change": 0.12,
        "upvol_pct": 75,
        "flow_ratio": 1.35,
        "iv_change": 0.05,
        "skew_shift": 0.06,
        "timestamp": int(time.time() * 1000),
        "seconds_since_open": 600,  # force MORNING (10 minutes)
        "chain": synthetic_chain(symbol, px),
    }


# ======================================================================
#  MAIN SIM LOOP
# ======================================================================
async def run_sim():
    symbols = ["SPY"]
    orch = build_orchestrator(symbols)

    await orch.engine.start()

    print("\n=== 🚀 FULL PIPELINE SIMULATION START ===\n")

    for px in [470.40, 470.55, 470.70]:
        print(f"\n--- 🔥 Feeding tick @ {px} ---")
        tick = make_tick("SPY", px)
        await orch.on_market_data(tick)
        await asyncio.sleep(0.3)

    print("\n=== SIM COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(run_sim())
