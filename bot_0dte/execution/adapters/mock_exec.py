"""
Mock execution adapter for local/offline strategy testing.
Simulates IBKR-style bracket order placement + market orders.
"""

import asyncio
import time
from typing import Dict, Any


# ============================================================
# MOCK ACCOUNT STATE (Required by Orchestrator)
# ============================================================
class MockAccountState:
    """
    Simulation-safe version of IBKRAccountState.

    The orchestrator only requires:
        • net_liq
        • is_fresh()

    Nothing else.
    """

    def __init__(self, net_liq: float = 25000):
        self.net_liq = net_liq
        self._fresh_ts = time.time()

    def is_fresh(self) -> bool:
        # Always fresh in simulation
        return True


# ============================================================
# EXISTING MOCK BRACKET EXECUTOR
# ============================================================
class MockExecAdapter:
    """
    Legacy dummy bracket-order adapter.
    """

    async def send_bracket(self, **order: Dict[str, Any]) -> Dict[str, Any]:

        print("\n[MOCK EXEC] ORDER RECEIVED →")
        for k, v in order.items():
            print(f"    {k}: {v}")

        await asyncio.sleep(0.05)

        return {
            "status": "sent",
            "order_id": 123456,
            "details": order,
        }


# ============================================================
# MOCK EXECUTION ENGINE FOR ORCHESTRATOR
# ============================================================
class MockExecutionEngine:
    """
    Orchestrator-compatible execution engine.

    Implements:
        send_market()

    And exposes:
        account_state (required by orchestrator)
    """

    def __init__(self):
        self.last_order_id = 100000
        self.account_state = MockAccountState()   # <── REQUIRED FIX

    async def send_market(self, symbol, side, qty, price=None, meta=None):
        print("\n[MOCK_EXEC] MARKET ORDER →")
        print(f"  symbol:  {symbol}")
        print(f"  side:    {side}")
        print(f"  qty:     {qty}")
        print(f"  price:   {price}")
        print(f"  meta:    {meta}")

        # Simulate 20ms latency
        await asyncio.sleep(0.02)

        self.last_order_id += 1
        fill_price = price or 0.50

        return {
            "status": "filled",
            "order_id": self.last_order_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "fill_price": fill_price,
            "meta": meta or {},
        }
