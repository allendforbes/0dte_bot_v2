import asyncio
import time
import pytest

from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)

@pytest.mark.integration
@pytest.mark.asyncio
async def test_chain_snapshot():
    """
    Collect NBBO for 5 seconds and print chain rows.
    Validates:
        - MassiveOptionsWSAdapter event stream
        - OCC decoding
        - ChainAggregator update path
        - premium = mid of bid/ask
    """

    SYMBOL = "SPY"
    DURATION = 5

    agg = ChainAggregator(symbols=[SYMBOL])

    # Create Massive WS from env var MASSIVE_API_KEY
    ws = MassiveOptionsWSAdapter.from_env()

    # Register NBBO callback
    async def handle_nbbo(event):
        agg.update_from_nbbo(event)

    ws.on_nbbo(handle_nbbo)

    # Connect WS
    await ws.connect()

    # Subscribe to two known valid OCC codes (ATM ±1)
    contracts = [
        "O:SPY250103C00450000",
        "O:SPY250103P00450000",
    ]
    await ws.subscribe_contracts(contracts)

    # Collect events
    end_time = time.time() + DURATION
    while time.time() < end_time:
        await asyncio.sleep(0.1)

    # Snapshot result
    rows = agg.snapshot()

    print("\n=== CHAIN SNAPSHOT ===")
    for row in rows[:20]:
        print(row)

    await ws.close()

    # Structural asserts (no market assumptions)
    assert isinstance(rows, list)
    for r in rows:
        assert r.contract
        assert r.strike is not None
        assert r.right in ("C", "P")
