"""
ContractEngine — Dynamic OCC Subscription Manager

Responsibilities:
    • Convert strikes → OCC codes
    • Generate ATM ±2 cluster per symbol
    • Auto-subscribe via MassiveOptionsWSAdapter
    • Update subscriptions on price moves
    • Re-subscribe on reconnect

OCC Format: O:<UNDERLYING><YYMMDD><C/P><STRIKE*1000 padded to 8>
Example: O:SPY241122C00450000 (SPY, Nov 22 2024, Call, $450 strike)
"""

import asyncio
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


class ContractEngine:
    """
    Dynamic OCC subscription manager.

    Listens to underlying ticks and maintains ATM cluster subscriptions.
    """

    def __init__(self, options_ws, expiry_map: Dict[str, str]):
        self.opt_ws = options_ws
        self.expiry_map = expiry_map

        self.current_subs: Dict[str, List[str]] = {}
        self.last_price: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # OCC Encoding
    # ------------------------------------------------------------------
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        """
        Encode option contract to OCC format.

        Args:
            symbol: "SPY"
            expiry: "2024-11-22"
            right: "C" or "P"
            strike: 450.0

        Returns:
            "O:SPY241122C00450000"
        """
        yyyy, mm, dd = expiry.split("-")
        yymmdd = yyyy[2:] + mm + dd
        strike_int = int(round(strike * 1000))
        strike_str = f"{strike_int:08d}"
        return f"O:{symbol.upper()}{yymmdd}{right.upper()}{strike_str}"

    # ------------------------------------------------------------------
    # ATM Strike Cluster
    # ------------------------------------------------------------------
    def _compute_strikes(self, price: float) -> List[float]:
        """
        Generate ATM ±2 strike cluster.

        Args:
            price: Current underlying price

        Returns:
            [ATM-2, ATM-1, ATM, ATM+1, ATM+2]
        """
        atm = round(price)
        return [atm - 2, atm - 1, atm, atm + 1, atm + 2]

    # ------------------------------------------------------------------
    # Main Trigger (called by MassiveMux)
    # ------------------------------------------------------------------
    async def on_underlying(self, event: Dict):
        """
        Receive normalized underlying event from MassiveMux.

        Event format:
        {
            "symbol": "SPY",
            "price": 450.25,
            "_recv_ts": 1234567890.123
        }
        """
        symbol = event.get("symbol")
        price = event.get("price")

        if not symbol or not price:
            return

        # Initialize if first time seeing this symbol
        if symbol not in self.last_price:
            self.last_price[symbol] = price
            self.current_subs[symbol] = []

        self.last_price[symbol] = price

        # Get expiry for this symbol
        expiry = self.expiry_map.get(symbol)
        if not expiry:
            return  # No expiry configured

        # Compute ATM cluster
        strikes = self._compute_strikes(price)
        occ_codes = []

        # Generate calls and puts for each strike
        for K in strikes:
            occ_codes.append(self.encode_occ(symbol, expiry, "C", K))
            occ_codes.append(self.encode_occ(symbol, expiry, "P", K))

        # Deduplicate
        occ_codes = sorted(list(set(occ_codes)))

        # Compare to existing subscriptions
        current = self.current_subs.get(symbol, [])
        if set(current) == set(occ_codes):
            return  # No change needed

        logger.info(
            f"[CONTRACT_ENGINE] Refreshing {symbol} → {len(occ_codes)} contracts"
        )

        # Update storage
        self.current_subs[symbol] = occ_codes

        # Send subscription to options WS
        await self.opt_ws.subscribe_contracts(occ_codes)

    # ------------------------------------------------------------------
    async def resubscribe_all(self):
        """
        Re-subscribe all contracts after options WS reconnect.
        """
        for symbol, occ_list in self.current_subs.items():
            if occ_list:
                logger.info(
                    f"[CONTRACT_ENGINE] Resubscribing {symbol} ({len(occ_list)} contracts)"
                )
                await self.opt_ws.subscribe_contracts(occ_list)
