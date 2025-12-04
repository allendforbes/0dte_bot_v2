"""
ChainAggregator v3.2 — A2-M Compatible
--------------------------------------
Upgrades in v3.2:
    • Same as v3.1 plus:
        - ChainSnapshot wrapper for orchestrator
        - Millisecond-level freshness for A2-M latency logic
        - get(symbol) alias for orchestrator compatibility
        - No behavioral impact to StrikeSelector
"""

import time
from typing import Dict, Any, List, Optional, Tuple


# ==========================================================
# A2-M ADDITION — ChainSnapshot wrapper
# ==========================================================
class ChainSnapshot:
    """
    Immutable-like container required by A2-M orchestrator.
    Provides:
        • rows (list of NBBO entries)
        • last_update_ts_ms (int)
        • is_fresh() API
    """

    def __init__(self, rows: List[Dict[str, Any]], last_ts: float):
        self.rows = rows
        self.last_update_ts_ms = int(last_ts * 1000)

    def is_fresh(self, now_ms: float, max_age_ms: int) -> bool:
        """A2-M freshness check in milliseconds."""
        return (now_ms - self.last_update_ts_ms) <= max_age_ms


# ==========================================================
# Main Aggregator
# ==========================================================
class ChainAggregator:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols

        # symbol → contract → row
        self.cache: Dict[str, Dict[str, Dict[str, Any]]] = {s: {} for s in symbols}

        # last tick timestamp per symbol
        # NOTE: stored in seconds; converted to ms when creating snapshot
        self.last_ts: Dict[str, float] = {s: 0.0 for s in symbols}

        # optional delta windows
        self.delta_windows: Dict[str, Tuple[float, float]] = {}

    # ----------------------------------------------------------------------
    # Configuration API
    # ----------------------------------------------------------------------
    def set_delta_window(self, symbol: str, low: float, high: float):
        """Register a delta window for delta-based strike filtering."""
        self.delta_windows[symbol] = (low, high)

    # ----------------------------------------------------------------------
    # OCC PARSING HELPERS
    # ----------------------------------------------------------------------
    @staticmethod
    def parse_occ_symbol(contract: str) -> Optional[str]:
        """
        Massive OCC always starts with the underlying root (3–4 chars).
        Root ends immediately before expiry YYMMDD.
        """
        if not contract:
            return None

        for i, ch in enumerate(contract):
            if ch.isdigit():
                return contract[:i]

        return None

    @staticmethod
    def parse_occ_strike(contract: str) -> Optional[float]:
        """OCC strike = last 8 digits, scaled by /1000."""
        if not contract or len(contract) < 20:
            return None
        try:
            return int(contract[-8:]) / 1000.0
        except Exception:
            return None

    @staticmethod
    def parse_occ_right(contract: str) -> Optional[str]:
        """
        Right = C or P, located immediately after expiry (6 digits).
        """
        if not contract:
            return None

        for i, ch in enumerate(contract):
            if ch.isdigit():
                expiry_start = i
                break
        else:
            return None

        idx = expiry_start + 6
        if idx < len(contract):
            return contract[idx]
        return None

    # ----------------------------------------------------------------------
    # MAIN UPDATE API
    # ----------------------------------------------------------------------
    def update_from_nbbo(self, event: Dict[str, Any]):
        contract = event.get("contract")
        if not contract:
            return

        # Symbol detection (never lowercase)
        symbol = (
            event.get("symbol")
            or event.get("sym")
            or self.parse_occ_symbol(contract)
        )
        if symbol not in self.symbols:
            return  # ignore irrelevant rows

        # Strike
        strike = event.get("strike")
        if strike is None:
            strike = self.parse_occ_strike(contract)

        # Right
        right = event.get("right")
        if right is None:
            right = self.parse_occ_right(contract)

        # Bid/Ask
        bid = event.get("bid")
        if bid is None:
            bid = event.get("bp")

        ask = event.get("ask")
        if ask is None:
            ask = event.get("ap")

        # Row assembly
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
            "volume": (
                event.get("volume")
                if event.get("volume") is not None
                else event.get("vol")
            ),
            "open_interest": (
                event.get("open_interest")
                if event.get("open_interest") is not None
                else event.get("oi")
            ),
            # preserve Massive timestamp if provided, otherwise now
            "_recv_ts": event.get("_recv_ts", time.time()),
        }

        self.cache[symbol][contract] = row
        self.last_ts[symbol] = time.time()

    # ----------------------------------------------------------------------
    # Freshness (legacy API — orchestrator uses ChainSnapshot instead)
    # ----------------------------------------------------------------------
    def is_fresh(self, symbol: str, threshold: float = 2.0) -> bool:
        return (time.time() - self.last_ts.get(symbol, 0.0)) <= threshold

    # ----------------------------------------------------------------------
    # Core chain extraction
    # ----------------------------------------------------------------------
    def _extract_chain_rows(self, symbol: str) -> List[Dict[str, Any]]:
        out = []

        delta_win = self.delta_windows.get(symbol)

        for row in self.cache.get(symbol, {}).values():

            bid = row.get("bid")
            ask = row.get("ask")
            if bid is None or ask is None:
                continue
            if bid <= 0 or ask <= 0:
                continue

            premium = (bid + ask) / 2

            # Optional A2-M delta-window filtering
            if delta_win and row.get("delta") is not None:
                d = abs(row["delta"])
                lo, hi = delta_win
                if not (lo <= d <= hi):
                    continue

            out.append(
                {
                    "symbol": symbol,
                    "strike": row["strike"],
                    "right": row["right"],
                    "premium": premium,
                    "bid": bid,
                    "ask": ask,
                    "contract": row["contract"],
                    "iv": row["iv"],
                    "delta": row["delta"],
                    "gamma": row["gamma"],
                    "theta": row["theta"],
                    "vega": row["vega"],
                    "volume": row["volume"],
                    "open_interest": row["open_interest"],
                    "_recv_ts": row["_recv_ts"],
                }
            )

        return out

    # ----------------------------------------------------------------------
    # A2-M: Return ChainSnapshot object
    # ----------------------------------------------------------------------
    def get_snapshot(self, symbol: str) -> Optional[ChainSnapshot]:
        if symbol not in self.symbols:
            return None

        rows = self._extract_chain_rows(symbol)
        last = self.last_ts.get(symbol, 0.0)

        return ChainSnapshot(rows, last)

    # Alias for orchestrator
    def get(self, symbol: str) -> Optional[ChainSnapshot]:
        return self.get_snapshot(symbol)

    # Legacy API from v3.1 (StrikeSelector still uses list of dicts)
    def get_chain(self, symbol: str) -> List[Dict[str, Any]]:
        return self._extract_chain_rows(symbol)

    # ----------------------------------------------------------------------
    def snapshot(self) -> List[Dict[str, Any]]:
        out = []
        for s in self.symbols:
            out.extend(self.get_chain(s))
        return out
