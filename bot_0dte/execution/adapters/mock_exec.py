"""
Mock execution adapter for local/offline strategy testing.
Simulates IBKR-style bracket order placement.
"""

import asyncio
from typing import Dict, Any


class MockExecAdapter:
    """
    A dummy execution adapter used when running in backtest/demo mode.
    """

    async def send_bracket(self, **order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Simulates sending a bracket order.
        Returns a fake order ID immediately.
        """

        print("\n[MOCK EXEC] ORDER RECEIVED →")
        for k, v in order.items():
            print(f"    {k}: {v}")

        # simulate network/IBKR latency
        await asyncio.sleep(0.05)

        return {
            "status": "sent",
            "order_id": 123456,
            "details": order,
        }

