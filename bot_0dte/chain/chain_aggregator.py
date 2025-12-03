"""
ChainAggregator — NBBO → normalized chain snapshot
--------------------------------------------------

Responsibilities:
    • Maintain a rolling map of OCC contract → latest NBBO
    • Normalize Massive NBBO fields to a consistent schema
    • Support greeks, IV, volume, OI
    • Provide chain snapshots for:
          - StrikeSelector
          - Orchestrator microstructure metrics
          - LatencyPrecheck
          - Trail logic

Design rules:
    • StrikeSelector expects: strike, right, bid, ask, premium(mid)
    • Orchestrator expects: volume, open_interest, iv, greeks
    • MassiveOptionsWSAdapter already pre-normalizes many fields
"""

import time
from typing import Dict, Any, List


class ChainAggregator:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.cache: Dict[str, Dict[str, Dict[str, Any]]] = {s: {} for s in symbols}
        self.last_ts: Dict[str, float] = {s: 0.0 for s in symbols}

    # ----------------------------------------------------------------------
    # DIRECT UPDATE (used by ContractEngine.on_nbbo or adapter)
    # ----------------------------------------------------------------------
    def update(self, event: Dict[str, Any]):
        symbol = event.get("symbol")
        contract = event.get("contract")

        if not symbol or contract is None:
            return

        self.cache[symbol][contract] = event
        self.last_ts[symbol] = time.time()

    # ----------------------------------------------------------------------
    # UPDATE FROM Massive NBBO (adapter-level normalization)
    # ----------------------------------------------------------------------
    def update_from_nbbo(self, event: Dict[str, Any]):
        """
        Takes events from MassiveOptionsWSAdapter._dispatch(),
        which already normalizes:
            symbol, contract, strike, right,
            bid/bp, ask/ap, greeks, volume, open_interest
        """
        contract = event.get("contract")
        symbol = event.get("symbol")

        if not symbol or not contract:
            return

        # safe strike extraction
        strike = event.get("strike")
        if strike is None:
            try:
                strike = int(contract[12:]) / 1000.0
            except Exception:
                strike = None

        right = event.get("right")
        if right is None and contract and len(contract) > 11:
            right = contract[11]

        # normalize bid/ask
        bid = event.get("bid") or event.get("bp")
        ask = event.get("ask") or event.get("ap")

        row = {
            "symbol": symbol,
            "contract": contract,
            "strike": strike,
            "right": right,
            "bid": bid,
            "ask": ask,
            "iv": event.get("iv"),
            "delta": event.get("delta"),
            "gamma": event.get("gamma"),
            "theta": event.get("theta"),
            "vega": event.get("vega"),
            "volume": event.get("volume") or event.get("vol"),
            "open_interest": event.get("open_interest") or event.get("oi"),
            "_recv_ts": event.get("_recv_ts", time.time()),
            "ev": event.get("ev"),
        }

        self.cache[symbol][contract] = row
        self.last_ts[symbol] = time.time()

    # ----------------------------------------------------------------------
    # FRESHNESS CHECK
    # ----------------------------------------------------------------------
    def is_fresh(self, symbol: str, threshold: float = 2.0) -> bool:
        """
        Massive NBBO feed is very fast. Freshness ensures:
            • Selector has up-to-date premiums
            • LatencyPrecheck sees correct spread / slippage
            • Signals aren't fired into stale chains
        """
        return (time.time() - self.last_ts.get(symbol, 0)) <= threshold

    # ----------------------------------------------------------------------
    # NORMALIZED SNAPSHOT
    # ----------------------------------------------------------------------
    def get_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Convert raw NBBO events into the structure expected by
        StrikeSelector + Orchestrator microstructure helpers.
        """
        out = []

        for row in self.cache.get(symbol, {}).values():
            bid = row.get("bid")
            ask = row.get("ask")

            if bid is None or ask is None or bid <= 0 or ask <= 0:
                continue

            premium = (bid + ask) / 2

            out.append(
                {
                    "symbol": symbol,
                    "strike": row.get("strike"),
                    "right": row.get("right"),
                    "premium": premium,
                    "bid": bid,
                    "ask": ask,
                    "contract": row.get("contract"),
                    "iv": row.get("iv"),
                    "delta": row.get("delta"),
                    "gamma": row.get("gamma"),
                    "theta": row.get("theta"),
                    "vega": row.get("vega"),
                    "volume": row.get("volume"),
                    "open_interest": row.get("open_interest"),
                    "_recv_ts": row.get("_recv_ts"),
                }
            )

        return out

    # ----------------------------------------------------------------------
    def snapshot(self) -> List[Dict[str, Any]]:
        """Flatten chain across all symbols (rarely used)."""
        out = []
        for s in self.symbols:
            out.extend(self.get_chain(s))
        return out
