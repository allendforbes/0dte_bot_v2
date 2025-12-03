import asyncio
import time
import pytest

from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter
from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine

def markets_open_now():
    """Return True only during market hours (ET)."""
    h = time.localtime().tm_hour
    return 9 <= h <= 16

# Mock adapter import (needed for after-hours)
from bot_0dte.data.providers.massive.mock_massive_ws_adapter import MockMassiveWSAdapter

@pytest.mark.integration
@pytest.mark.asyncio
async def test_massive_contract_engine():
    """
    Integration test:
        - MassiveContractEngine initializes
        - Contract list loads
        - Subscriptions are sent
        - NBBO events flow into ChainAggregator
        - Snapshot is valid

    Duration: ~5 seconds
    """

    SYMBOL = "SPY"
    DURATION = 5

    # Shared aggregator for engine + callbacks
    agg = ChainAggregator(symbols=[SYMBOL])

    # Live WS using MASSIVE_API_KEY
    ws = MassiveOptionsWSAdapter.from_env() if markets_open_now() else MockMassiveWSAdapter()

    # Contract engine using WS + aggregator
    engine = MassiveContractEngine(
        symbol=SYMBOL,
        ws=ws,
        chain=agg
    )

    # Ensure NBBO updates flow into aggregator
    async def handle_nbbo(event):
        agg.update_from_nbbo(event)

    ws.on_nbbo(handle_nbbo)

    # Reconnect handler should rebuild contracts
    ws.on_reconnect(engine.handle_reconnect)

    # Connect to Massive
    await ws.connect()

    # Force initial contract load
    await engine.refresh_contracts()

    assert len(engine.contracts) > 0, "ContractEngine did not load any contracts"

    # Subscribe to all contracts returned
    await engine.subscribe_all()

    # Collect NBBO events for a few seconds
    end_t = time.time() + DURATION
    while time.time() < end_t:
        await asyncio.sleep(0.1)

    # Snapshot chain
    rows = agg.get_chain(SYMBOL)

    assert isinstance(rows, list), "Snapshot is not a list"
    assert len(rows) > 0, "No NBBO rows received"

    # Print first 20 rows for verification
    print("\n=== CONTRACT ENGINE SNAPSHOT ===")
    for r in rows[:20]:
        print(
            f"{r['symbol']} {r['right']} {r['strike']} "
            f"bid={r['bid']} ask={r['ask']} premium={r['premium']} "
            f"{r['contract']}"
        )

    # Clean close
    await ws.close()
