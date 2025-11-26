"""
ContractEngine — Dynamic OCC Subscription Manager (Quiet, Production-Safe)

Responsibilities:
    • Convert strikes → OCC codes
    • Generate ATM ±2 cluster per symbol
    • Auto-subscribe via MassiveOptionsWSAdapter
    • Update subscriptions on price moves (debounced)
    • Re-subscribe on reconnect

OCC Format: O:<UNDERLYING><YYMMDD><C/P><STRIKE*1000 padded to 8>
Example: O:SPY241122C00450000
"""

import asyncio
import logging
import math
import time
from collections import defaultdict
from typing import List, Dict

logger = logging.getLogger(__name__)


class ContractEngine:
    """
    Maintains active OCC subscription set based on underlying price.
    """

    def __init__(self, options_ws, expiry_map: Dict[str, str]):
        self.opt_ws = options_ws
        self.expiry_map = expiry_map

        # symbol → [occ codes...]
        self.current_subs: Dict[str, List[str]] = {}

        # last seen underlying price
        self.last_price: Dict[str, float] = {}

        # concurrency & rate limiting
        self._locks = defaultdict(asyncio.Lock)          # per-symbol lock
        self._last_refresh_ts: Dict[str, float] = {}     # throttle per symbol
        self._min_refresh_interval = 1.0                 # seconds (safe for Massive)

    # ------------------------------------------------------------------
    # OCC Encoding
    # ------------------------------------------------------------------
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        """
        Convert option attributes into Massive OCC format.
        """
        yyyy, mm, dd = expiry.split("-")
        yymmdd = yyyy[2:] + mm + dd

        strike_int = int(round(strike * 1000))
        strike_str = f"{strike_int:08d}"

        return f"O:{symbol.upper()}{yymmdd}{right.upper()}{strike_str}"

    # ------------------------------------------------------------------
    # Strike Cluster
    # ------------------------------------------------------------------
    def _compute_strikes(self, price: float) -> List[float]:
        """
        ATM ±2 using floor — avoids noisy 0.50 flips.
        """
        atm = math.floor(price)
        return [atm - 2, atm - 1, atm, atm + 1, atm + 2]

    # ------------------------------------------------------------------
    # Main Trigger — called by MassiveMux per underlying tick
    # ------------------------------------------------------------------
    async def on_underlying(self, event: Dict):
        symbol = event.get("symbol")
        price = event.get("price")

        if not symbol or price is None:
            return

        # serialize refreshes per symbol
        async with self._locks[symbol]:
            expiry = self.expiry_map.get(symbol)
            if not expiry:
                return

            # First-time symbol init
            if symbol not in self.last_price:
                self.last_price[symbol] = price
                self.current_subs[symbol] = []
                self._last_refresh_ts[symbol] = 0.0

            self.last_price[symbol] = price

            # compute cluster
            strikes = self._compute_strikes(price)
            occ_codes = []

            for K in strikes:
                occ_codes.append(self.encode_occ(symbol, expiry, "C", K))
                occ_codes.append(self.encode_occ(symbol, expiry, "P", K))

            new_set = frozenset(occ_codes)
            cur_set = frozenset(self.current_subs.get(symbol, []))

            # guard: unchanged
            if new_set == cur_set:
                return

            # guard: throttle refreshes (1s)
            now = time.monotonic()
            last = self._last_refresh_ts.get(symbol, 0.0)
            if (now - last) < self._min_refresh_interval:
                return

            # commit before async I/O
            new_list = sorted(new_set)
            self.current_subs[symbol] = new_list
            self._last_refresh_ts[symbol] = now

            logger.info(
                f"[CONTRACT_ENGINE] Refreshing {symbol} → {len(new_list)} contracts"
            )

            await self.opt_ws.subscribe_contracts(new_list)

    # ------------------------------------------------------------------
    # Re-subscribe after Massive reconnect
    # ------------------------------------------------------------------
    async def resubscribe_all(self):
        """
        Send full subscription list back to Massive after reconnect.
        """
        for symbol, occ_list in self.current_subs.items():
            if occ_list:
                logger.info(
                    f"[CONTRACT_ENGINE] Resubscribing {symbol} ({len(occ_list)} contracts)"
                )
                await self.opt_ws.subscribe_contracts(occ_list)
