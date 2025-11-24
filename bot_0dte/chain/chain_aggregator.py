"""
Chain Aggregator (NBBO → normalized chain snapshot)
"""

import time
from typing import Dict, Any, List


class ChainAggregator:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.cache = {s: {} for s in symbols}
        self.last_ts = {s: 0.0 for s in symbols}

    def update(self, event: Dict[str, Any]):
        sym = event.get("symbol")
        contract = event.get("contract")
        if not sym or not contract:
            return

        self.cache[sym][contract] = event
        self.last_ts[sym] = time.time()

    def is_fresh(self, symbol: str, threshold: float = 2.0) -> bool:
        return (time.time() - self.last_ts.get(symbol, 0)) <= threshold

    def get_chain(self, symbol: str) -> List[Dict[str, Any]]:
        out = []
        for row in self.cache.get(symbol, {}).values():
            bid = row.get("bid", 0)
            ask = row.get("ask", 0)
            mid = (bid + ask) / 2 if (bid and ask) else 0

            out.append(
                {
                    "symbol": symbol,
                    "strike": row.get("strike"),
                    "right": row.get("right"),
                    "premium": mid,
                    "bid": bid,
                    "ask": ask,
                    "contract": row.get("contract"),
                }
            )
        return out
