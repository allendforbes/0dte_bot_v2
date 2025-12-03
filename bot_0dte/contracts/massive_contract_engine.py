"""
MassiveContractEngine — Dynamic OCC Subscription Manager
Works with:
    • IBKR underlying feed via MassiveMux
    • MassiveOptionsWSAdapter (NBBO)
    • Orchestrator (for chain freshness)
    • Your universe.py expiry rules
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List, Any

from bot_0dte.universe import get_expiry_for_symbol

logger = logging.getLogger(__name__)


class MassiveContractEngine:
    """
    Subscription logic:

        underlying tick → compute ATM → build small cluster (ATM ± 1)
                        → generate OCC codes
                        → subscribe via Massive WS adapter

    OCC strings example:
        O:SPY250103C00480000
    """

    def __init__(self, symbol: str, ws, chain):
        self.symbol = symbol
        self.ws = ws          # MassiveOptionsWSAdapter
        self.chain = chain    # ChainAggregator

        # Expiry is dynamic and provided by universe.py
        self.expiry = get_expiry_for_symbol(symbol)

        self.last_price: Dict[str, float] = {}
        self.current_subs: Dict[str, List[str]] = {}

        # Prevents excessive refreshes
        self._last_refresh_ts: Dict[str, float] = {}
        self._min_refresh_interval = 7.0  # Massive-safe

        # Locks per symbol
        self._locks = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def contracts(self) -> List[str]:
        return self.current_subs.get(self.symbol, [])

    # ------------------------------------------------------------------
    # OCC Encoding
    # ------------------------------------------------------------------
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        """
        Convert:
            SPY, 2025-01-03, C, 480.0
        → OCC:
            O:SPY250103C00480000
        """
        yyyy, mm, dd = expiry.split("-")
        yymmdd = yyyy[2:] + mm + dd

        strike_int = int(round(strike * 1000))
        strike_str = f"{strike_int:08d}"

        return f"O:{symbol.upper()}{yymmdd}{right.upper()}{strike_str}"

    # ------------------------------------------------------------------
    def _compute_strikes(self, price: float) -> List[float]:
        """
        Minimal clusters (ATM ± 1) — fast, low-bandwidth.
        """
        atm = round(price)
        return [atm - 1, atm, atm + 1]

    # ------------------------------------------------------------------
    async def on_nbbo(self, event: Dict[str, Any]):
        """Forward NBBO into chain aggregator."""
        self.chain.update(event)

    # ------------------------------------------------------------------
    async def on_underlying(self, event: Dict[str, Any]):
        """
        Called on every underlying tick (SPY, QQQ, etc.)
        Only rebuild subscriptions when a *new* ATM level is reached.
        """
        symbol = event.get("symbol")
        price = event.get("price")

        if symbol != self.symbol or price is None:
            return

        async with self._locks[symbol]:

            prev_price = self.last_price.get(symbol)
            self.last_price[symbol] = price

            # If expiry changed (Thu/Fri WEEKLIES), refresh immediately
            new_expiry = get_expiry_for_symbol(symbol)
            if new_expiry != self.expiry:
                logger.info(f"[ENGINE] Updated expiry {self.expiry} → {new_expiry}")
                self.expiry = new_expiry
                await self._refresh_now(price)
                return

            # No previous price? initialize
            if prev_price is None:
                await self._refresh_now(price)
                return

            # Rebuild only on ATM change
            old_atm = round(prev_price)
            new_atm = round(price)

            if abs(new_atm - old_atm) < 1:
                return

            # Respect refresh cooldown
            now = time.monotonic()
            if now - self._last_refresh_ts.get(symbol, 0) < self._min_refresh_interval:
                return

            await self._refresh_now(price)

    # ------------------------------------------------------------------
    async def _refresh_now(self, price: float):
        """
        Computes strikes → builds OCC → subscribes.
        """
        if not self.expiry:
            logger.warning(f"[ENGINE] No valid expiry for {self.symbol} — skipping OCC refresh")
            return

        strikes = self._compute_strikes(price)

        occ_list = []
        for K in strikes:
            occ_list.append(self.encode_occ(self.symbol, self.expiry, "C", K))
            occ_list.append(self.encode_occ(self.symbol, self.expiry, "P", K))

        occ_list = sorted(occ_list)

        # Prevent duplicate subscriptions
        if occ_list == self.current_subs.get(self.symbol):
            return

        logger.info(f"[ENGINE] New OCC set {self.symbol} @ {price} → {occ_list}")

        self.current_subs[self.symbol] = occ_list
        self._last_refresh_ts[self.symbol] = time.monotonic()

        await self.ws.subscribe_contracts(occ_list)

    # ------------------------------------------------------------------
    async def refresh_contracts(self):
        """
        Called once after Massive WS connects.
        Must initialize the very first ATM cluster.
        """
        price = self.last_price.get(self.symbol, None)
        if price is None:
            # Tests or cold-start fallback
            price = 480.0

        return await self._refresh_now(price)

    # ------------------------------------------------------------------
    async def resubscribe_all(self):
        """
        Called on Massive reconnect.
        """
        lst = self.current_subs.get(self.symbol, [])
        if lst:
            await self.ws.subscribe_contracts(lst)

    # alias for orchestrator
    async def subscribe_all(self):
        await self.resubscribe_all()

    async def handle_reconnect(self):
        await self.resubscribe_all()
