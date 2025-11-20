import asyncio

from bot_0dte.control.session_controller import SessionController
from bot_0dte.execution.adapters.mock_exec import MockExecAdapter
from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.orchestrator import Orchestrator
from bot_0dte.data.providers.marketdata.marketdata_feed import MarketDataFeed
from bot_0dte.data.adapters.ibkr_chain_bridge import IBKRChainBridge
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


async def test():
    print("\n=== TEST START ===\n")

    # ------------------------------------------
    # 1. Build all components manually (mock mode)
    # ------------------------------------------
    logger = StructuredLogger()
    telemetry = Telemetry()

    mock_exec = MockExecAdapter()

    engine = ExecutionEngine(use_mock=True)
    await engine.start()

    chain_bridge = IBKRChainBridge(ib=None, journaling_cb=None)

    feed = MarketDataFeed(api_key="FAKE_API_KEY")

    orch = Orchestrator(
        engine=engine,
        chain_bridge=chain_bridge,
        feed=feed,
        telemetry=telemetry,
        logger=logger,
    )

    # connect feed callback → orchestrator
    feed.callback = orch.on_market_data

    # ------------------------------------------
    # 2. Create fake MD.app tick
    # ------------------------------------------
    fake_tick = {
        "symbol": "SPY",
        "price": 470.40,
        "bid": 470.35,
        "ask": 470.45,
        "vwap": 470.10,
        "vwap_dev_change": 0.04,
        "upvol_pct": 60,
        "flow_ratio": 1.2,
        "iv_change": 0.015,
        "skew_shift": 0.02,
        # Our option chain (normally from MD.app)
        "chain": [
            {
                "symbol": "SPY",
                "expiry": "20251119",
                "strike": 470,
                "right": "C",
                "bid": 0.95,
                "ask": 1.05,
                "last": 1.00,
                "iv": 0.14,
            },
            {
                "symbol": "SPY",
                "expiry": "20251119",
                "strike": 471,
                "right": "C",
                "bid": 0.65,
                "ask": 0.75,
                "last": 0.70,
                "iv": 0.15,
            },
        ],
    }

    # ------------------------------------------
    # 3. Run one tick through the whole system
    # ------------------------------------------
    print("[TEST] Sending fake tick to orchestrator...\n")
    await orch.on_market_data(fake_tick)

    print("\n=== TEST COMPLETE ===\n")


if __name__ == "__main__":
    asyncio.run(test())
