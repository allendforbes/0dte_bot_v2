"""
ContractEngine — Dynamic OCC Subscription Manager (Quiet, Production-Safe)

Responsibilities:
    • Convert strikes → OCC codes
    • Generate ATM ±1 strike cluster (round-based, reduced flicker)
    • Auto-subscribe via MassiveOptionsWSAdapter (Q.O prefix now handled there)
    • Update subscriptions on price moves (debounced, throttled)
    • Re-subscribe automatically after Massive reconnect

This engine is intentionally conservative to avoid Massive policy violations.
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

    Triggered on every underlying tick:
        MassiveMux → on_underlying() → compute cluster → subscribe if needed
    """

    def __init__(self, options_ws, expiry_map: Dict[str, str]):
        self.opt_ws = options_ws
        self.expiry_map = expiry_map

        # symbol → [occ codes...]
        self.current_subs: Dict[str, List[str]] = {}

        # last seen underlying price
        self.last_price: Dict[str, float] = {}

        # concurrency & rate limiting
        self._locks = defaultdict(asyncio.Lock)          # per-symbol update lock
        self._last_refresh_ts: Dict[str, float] = {}     # refresh throttle per symbol

        # Much safer for Massive (was 3.0)
        self._min_refresh_interval = 7.0                 # seconds (Massive-safe)

    # ------------------------------------------------------------------
    # OCC Encoding
    # ------------------------------------------------------------------
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        """
        Convert option attributes into Massive OCC format.

        Example:
            O:SPY241122C00450000
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
        Compute an ATM ±1 cluster using round() instead of floor()
        to reduce ATM flicker when trading around .50 boundaries.

        Example:
            price = 681.42 → atm=681 → [680, 681, 682]
            price = 681.51 → atm=682 → [681, 682, 683]
        """
        atm = round(price)
        return [atm - 1, atm, atm + 1]

    # ------------------------------------------------------------------
    # Main Trigger — called by MassiveMux per underlying tick
    # ------------------------------------------------------------------
    async def on_underlying(self, event: Dict):
        """
        Underlying tick handler:
            • compute new cluster
            • throttle contract refresh
            • refresh only when ATM truly moves
            • subscribe to new cluster if changed
        """
        symbol = event.get("symbol")
        price = event.get("price")

        if not symbol or price is None:
            return

        async with self._locks[symbol]:

            expiry = self.expiry_map.get(symbol)
            if not expiry:
                return

            prev_price = self.last_price.get(symbol)
            new_atm = round(price)

            # ------------------------------------------------------
            # NEW: Only refresh if ATM changes by ≥ 1 full strike
            # ------------------------------------------------------
            if prev_price is not None:
                prev_atm = round(prev_price)
                if abs(new_atm - prev_atm) < 1:
                    return

            # Update last price
            self.last_price[symbol] = price

            # Throttle updates per Massive safety limits
            now = time.monotonic()
            last = self._last_refresh_ts.get(symbol, 0.0)
            if (now - last) < self._min_refresh_interval:
                return

            # Build strike cluster
            strikes = self._compute_strikes(price)
            occ_codes = []
            for K in strikes:
                occ_codes.append(self.encode_occ(symbol, expiry, "C", K))
                occ_codes.append(self.encode_occ(symbol, expiry, "P", K))

            # Debug print (safe; very useful)
            print("[DEBUG_OCC]", symbol, expiry, strikes, occ_codes)

            new_set = frozenset(occ_codes)
            cur_set = frozenset(self.current_subs.get(symbol, []))

            # No change → skip
            if new_set == cur_set:
                return

            # Commit updated set
            new_list = sorted(new_set)
            self.current_subs[symbol] = new_list
            self._last_refresh_ts[symbol] = now

            logger.info(
                f"[CONTRACT_ENGINE] Refreshing {symbol} → {len(new_list)} contracts"
            )

            # Perform subscription
            await self.opt_ws.subscribe_contracts(new_list)

    # ------------------------------------------------------------------
    # Re-subscribe after Massive reconnect
    # ------------------------------------------------------------------
    async def resubscribe_all(self):
        """
        Called automatically after MassiveOptionsWSAdapter reconnect.
        Resends full subscription set for every symbol.
        """
        for symbol, occ_list in self.current_subs.items():
            if not occ_list:
                continue

            logger.info(
                f"[CONTRACT_ENGINE] Resubscribing {symbol} ({len(occ_list)} contracts)"
            )
            await self.opt_ws.subscribe_contracts(occ_list)
