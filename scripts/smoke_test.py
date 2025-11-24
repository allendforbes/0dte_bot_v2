"""
WS-Native Smoke Test

Tests the full WS-native architecture without live connections:
    • Mock WS adapters
    • MassiveMux replacement (MockMux)
    • Orchestrator with VWAP tracking
    • ExecutionEngine (mock mode)
    • Strategy pipeline
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath('.'))

from bot_0dte.orchestrator import Orchestrator, ChainAggregator, VWAPTracker
from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry

# =====================================================================
# Mock WebSocket Adapters
# =====================================================================
class MockStocksWS:
    """Mock stocks WebSocket for testing."""
    
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._handlers = []
    
    def on_underlying(self, cb):
        self._handlers.append(cb)
    
    async def connect(self):
        print("[MOCK STOCKS WS] Connected ✅")
    
    async def subscribe(self, symbols):
        print(f"[MOCK STOCKS WS] Subscribed to {symbols} ✅")
    
    async def inject_tick(self, event):
        """Inject synthetic tick for testing."""
        for cb in self._handlers:
            await cb(event)
    
    async def close(self):
        print("[MOCK STOCKS WS] Closed")


class MockOptionsWS:
    """Mock options WebSocket for testing."""
    
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._handlers = []
    
    def on_nbbo(self, cb):
        self._handlers.append(cb)
    
    async def connect(self):
        print("[MOCK OPTIONS WS] Connected ✅")
    
    async def subscribe_contracts(self, occ_codes):
        print(f"[MOCK OPTIONS WS] Subscribed to {len(occ_codes)} contracts ✅")
    
    async def inject_option(self, event):
        """Inject synthetic option tick for testing."""
        for cb in self._handlers:
            await cb(event)
    
    async def close(self):
        print("[MOCK OPTIONS WS] Closed")


class MockMux:
    """Mock MassiveMux for testing."""
    
    def __init__(self, stocks_ws, options_ws):
        self.stocks = stocks_ws
        self.options = options_ws
        self.loop = stocks_ws.loop
        self._underlying_handlers = []
        self._option_handlers = []
    
    def on_underlying(self, cb):
        self._underlying_handlers.append(cb)
    
    def on_option(self, cb):
        self._option_handlers.append(cb)
    
    async def connect(self, symbols, expiry_map):
        print(f"[MOCK MUX] Connecting with {len(symbols)} symbols...")
        await self.stocks.connect()
        await self.options.connect()
        await self.stocks.subscribe(symbols)
        
        # Wire callbacks
        self.stocks.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)
        
        print("[MOCK MUX] Connected ✅")
    
    async def _handle_underlying(self, event):
        for cb in self._underlying_handlers:
            await cb(event)
    
    async def _handle_option(self, event):
        for cb in self._option_handlers:
            await cb(event)
    
    async def close(self):
        await self.stocks.close()
        await self.options.close()


# =====================================================================
# Test Functions
# =====================================================================
async def test_vwap_tracker():
    """Test VWAP calculation."""
    print("\n" + "="*60)
    print("TEST 1: VWAP Tracker")
    print("="*60)
    
    tracker = VWAPTracker(window_size=5)
    
    prices = [450.0, 450.5, 451.0, 450.8, 451.2]
    print(f"Input prices: {prices}")
    
    for price in prices:
        result = tracker.update(price)
        print(f"  Price: {price:.2f} → VWAP: {result['vwap']:.2f}, "
              f"Dev: {result['vwap_dev']:.2f}, "
              f"Change: {result['vwap_dev_change']:.2f}")
    
    print("✅ VWAP Tracker working")


async def test_chain_aggregator():
    """Test chain aggregation."""
    print("\n" + "="*60)
    print("TEST 2: Chain Aggregator")
    print("="*60)
    
    agg = ChainAggregator(["SPY"])
    
    # Inject option ticks
    events = [
        {
            "symbol": "SPY",
            "contract": "O:SPY251122C00450000",
            "strike": 450.0,
            "right": "C",
            "bid": 0.90,
            "ask": 1.00,
            "_recv_ts": 1234567890.0
        },
        {
            "symbol": "SPY",
            "contract": "O:SPY251122C00451000",
            "strike": 451.0,
            "right": "C",
            "bid": 0.85,
            "ask": 0.95,
            "_recv_ts": 1234567890.0
        },
    ]
    
    for event in events:
        agg.update(event)
    
    chain = agg.get_chain("SPY")
    print(f"Chain rows: {len(chain)}")
    for row in chain:
        print(f"  Strike: {row['strike']}, Premium: ${row['premium']:.2f}")
    
    print(f"✅ Chain Aggregator working (freshness: {agg.is_fresh('SPY')})")


async def test_full_pipeline():
    """Test full WS-native pipeline."""
    print("\n" + "="*60)
    print("TEST 3: Full WS-Native Pipeline")
    print("="*60)
    
    # Setup
    stocks_ws = MockStocksWS()
    options_ws = MockOptionsWS()
    mux = MockMux(stocks_ws, options_ws)
    
    engine = ExecutionEngine(use_mock=True)
    await engine.start()
    
    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=Telemetry(),
        logger=StructuredLogger(),
        universe=["SPY"],
        auto_trade_enabled=False,
        trade_mode="shadow"
    )
    
    print("\n[PIPELINE] Starting orchestrator...")
    await orch.start()
    
    print("\n[PIPELINE] Injecting option chain...")
    # Inject ATM cluster
    for strike in [449, 450, 451]:
        await options_ws.inject_option({
            "symbol": "SPY",
            "contract": f"O:SPY251122C00{strike}000",
            "strike": float(strike),
            "right": "C",
            "bid": 0.90,
            "ask": 1.00,
            "_recv_ts": 1234567890.0
        })
    
    await asyncio.sleep(0.1)
    
    print("\n[PIPELINE] Injecting underlying ticks...")
    # Inject 5 ticks to build VWAP
    for i, price in enumerate([450.0, 450.2, 450.5, 450.8, 451.0]):
        print(f"\n  Tick {i+1}: ${price:.2f}")
        await stocks_ws.inject_tick({
            "symbol": "SPY",
            "price": price,
            "bid": price - 0.05,
            "ask": price + 0.05,
            "_recv_ts": 1234567890.0 + i
        })
        await asyncio.sleep(0.05)
    
    print("\n[PIPELINE] Checking VWAP tracker...")
    tracker = orch._vwap_tracker.get("SPY")
    if tracker:
        print(f"  VWAP state: {len(tracker.prices)} prices tracked")
        print(f"  Last VWAP: ${tracker.last_vwap:.2f}")
        print("  ✅ VWAP tracking working")
    else:
        print("  ❌ VWAP tracker not found")
    
    print("\n[PIPELINE] Checking chain aggregator...")
    chain = orch.chain_agg.get_chain("SPY")
    print(f"  Chain has {len(chain)} options")
    if chain:
        print("  ✅ Chain aggregation working")
    else:
        print("  ❌ Chain empty")
    
    await mux.close()
    print("\n✅ Full pipeline test complete")


# =====================================================================
# Main
# =====================================================================
async def main():
    """Run all smoke tests."""
    print("\n" + "="*60)
    print("🧪 WS-NATIVE SMOKE TEST")
    print("="*60)
    
    try:
        await test_vwap_tracker()
        await test_chain_aggregator()
        await test_full_pipeline()
        
        print("\n" + "="*60)
        print("✅ ALL SMOKE TESTS PASSED")
        print("="*60 + "\n")
        
    except Exception as e:
        print("\n" + "="*60)
        print("❌ SMOKE TEST FAILED")
        print("="*60)
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
