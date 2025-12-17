import time
from typing import Dict, Any, List, Optional, Tuple


class ChainSnapshot:
    def __init__(self, rows: List[Dict[str, Any]], last_ts: float):
        self.rows = rows
        self.last_update_ts_ms = int(last_ts * 1000)

    def is_fresh(self, now_ms: float, max_age_ms: int) -> bool:
        return (now_ms - self.last_update_ts_ms) <= max_age_ms


class ChainAggregator:
    """
    v4.0 — REST-hydrated edition
    --------------------------------------
    Massive WS now ONLY gives NBBO.
    REST gives Greeks/IV/OI/volume.

    This aggregator:
      ✓ Accepts hydrated events (NBBO + REST injected)
      ✓ Ignores unhydrated events
      ✓ Preserves proper microstructure
    """

    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.cache: Dict[str, Dict[str, Dict[str, Any]]] = {s: {} for s in symbols}
        self.last_ts: Dict[str, float] = {s: 0.0 for s in symbols}
        self.delta_windows: Dict[str, Tuple[float, float]] = {}

    def set_delta_window(self, symbol: str, low: float, high: float):
        self.delta_windows[symbol] = (low, high)

    # ------------------ OCC HELPERS ------------------
    @staticmethod
    def parse_occ_symbol(contract: str) -> Optional[str]:
        if not contract:
            return None
        for i, ch in enumerate(contract):
            if ch.isdigit():
                return contract[:i]
        return None

    @staticmethod
    def parse_occ_strike(contract: str) -> Optional[float]:
        if not contract or len(contract) < 20:
            return None
        try:
            return int(contract[-8:]) / 1000.0
        except:
            return None

    @staticmethod
    def parse_occ_right(contract: str) -> Optional[str]:
        if not contract:
            return None
        for i, ch in enumerate(contract):
            if ch.isdigit():
                expiry_start = i
                break
        else:
            return None
        idx = expiry_start + 6
        return contract[idx] if idx < len(contract) else None

    # ============================================================
    #   MAIN NBBO UPDATE — Accepts NBBO-only or hydrated events
    # ============================================================
    def update_from_nbbo(self, event: Dict[str, Any]):
        """
        Accepts NBBO events with or without Greeks/IV.
        - Pure NBBO: Creates row with Greeks = None
        - Hydrated NBBO: Full row with Greeks/IV
        
        This allows the chain to populate from WebSocket immediately.
        """
        contract = event.get("contract")
        if not contract:
            return

        symbol = (
            event.get("symbol")
            or event.get("sym")
            or self.parse_occ_symbol(contract)
        )
        if symbol not in self.symbols:
            return

        strike = event.get("strike") or self.parse_occ_strike(contract)
        right = event.get("right") or self.parse_occ_right(contract)

        bid = event.get("bid") or event.get("bp")
        ask = event.get("ask") or event.get("ap")

        if not bid or not ask or bid <= 0 or ask <= 0:
            return

        premium = (bid + ask) / 2

        # ---- Greeks from event (may be None for pure NBBO) ----
        iv = event.get("iv")
        delta = event.get("delta")
        gamma = event.get("gamma")
        volume = event.get("volume")
        oi = event.get("open_interest") or event.get("oi")

        # Note: Greeks may be None for pure NBBO events
        # They will be enriched later by update_from_snapshot or future NBBO updates

        row = {
            "symbol": symbol,
            "contract": contract,
            "strike": strike,
            "right": right,
            "bid": bid,
            "ask": ask,
            "premium": premium,
            "iv": iv,
            "delta": delta,
            "gamma": gamma,
            "theta": event.get("theta"),
            "vega": event.get("vega"),
            "volume": volume,
            "open_interest": oi,
            "_recv_ts": event.get("_recv_ts", time.time()),
        }

        self.cache[symbol][contract] = row
        self.last_ts[symbol] = time.time()

    # ============================================================
    #   REST SNAPSHOT MERGE — called by MassiveContractEngine v4.0
    # ============================================================
    def update_from_snapshot(self, symbol: str, contract: str, snap: Dict[str, Any]):
        """
        Merge REST Greeks/IV/OI/volume into an existing NBBO row.
        Creates the row if needed (but will only become usable once NBBO arrives).

        REST payload example:
            {
                "iv": 0.22,
                "delta": 0.41,
                "gamma": 0.032,
                "theta": -0.05,
                "vega": 0.12,
                "open_interest": 8211,
                "volume": 1192
            }
        """

        if symbol not in self.symbols:
            return None

        book = self.cache[symbol]

        # If row exists (NBBO arrived), enrich it
        row = book.get(contract)
        if row:
            # Inject REST Greeks / IV
            for k in ("iv", "delta", "gamma", "theta", "vega",
                    "open_interest", "volume"):
                val = snap.get(k)
                if val is not None:
                    row[k] = val

            # mark as hydrated
            row["_hydrated"] = True
            row["_hydrated_ts"] = time.time()

            self.last_ts[symbol] = time.time()
            return row

        # NBBO hasn't arrived yet → create a placeholder
        # This allows partial hydration before NBBO completes the row.
        new_row = {
            "symbol": symbol,
            "contract": contract,
            "strike": self.parse_occ_strike(contract),
            "right": self.parse_occ_right(contract),
            "bid": None,
            "ask": None,
            "premium": None,

            # REST fields
            "iv": snap.get("iv"),
            "delta": snap.get("delta"),
            "gamma": snap.get("gamma"),
            "theta": snap.get("theta"),
            "vega": snap.get("vega"),
            "volume": snap.get("volume"),
            "open_interest": snap.get("open_interest"),

            "_hydrated": True,
            "_hydrated_ts": time.time(),
        }

        book[contract] = new_row
        self.last_ts[symbol] = time.time()
        return new_row        

    # ============================================================
    def is_fresh(self, symbol: str, threshold: float = 2.0) -> bool:
        return (time.time() - self.last_ts.get(symbol, 0.0)) <= threshold

    # ============================================================
    def _extract_chain_rows(self, symbol: str) -> List[Dict[str, Any]]:
        out = []
        delta_win = self.delta_windows.get(symbol)

        for row in self.cache.get(symbol, {}).values():
            bid, ask = row.get("bid"), row.get("ask")
            if not bid or not ask or bid <= 0 or ask <= 0:
                continue

            premium = row.get("premium") or (bid + ask) / 2

            if delta_win:
                d = row.get("delta")
                if d is None:
                    continue
                d = abs(d)
                lo, hi = delta_win
                if not (lo <= d <= hi):
                    continue

            out.append(row)

        return out

    # ============================================================
    def get_snapshot(self, symbol: str) -> Optional[ChainSnapshot]:
        if symbol not in self.symbols:
            return None
        rows = self._extract_chain_rows(symbol)
        last = self.last_ts.get(symbol, 0.0)
        return ChainSnapshot(rows, last)

    def get(self, symbol: str):
        return self.get_snapshot(symbol)

    def get_chain(self, symbol: str):
        return self._extract_chain_rows(symbol)

    def snapshot(self):
        out = []
        for s in self.symbols:
            out.extend(self.get_chain(s))
        return out