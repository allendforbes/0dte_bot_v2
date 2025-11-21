# bot_0dte/data/adapters/massive_contract_engine.py

import asyncio
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


class ContractEngine:
    """
    ContractEngine
    --------------
    Responsibilities:
        • Convert strikes → OCC codes
        • Generate ATM, ATM±1, ATM±2 cluster per symbol
        • Auto-subscribe via MassiveOptionsWSAdapter
        • Re-subscribe on reconnect signals
        • Maintain current subscription set

    Notes:
        - Expiry format from orchestrator: YYYY-MM-DD
        - OCC contract format:
              O:<UNDERLYING><YYMMDD><C/P><STRIKE*1000 padded to 8 digits>

          e.g. strike=450, call:
              O:SPY241122C00450000
    """

    def __init__(self, options_ws, stocks_ws, orchestrator):
        self.opt_ws = options_ws
        self.stk_ws = stocks_ws
        self.orch = orchestrator

        self.current_subs: Dict[str, List[str]] = {s: [] for s in orchestrator.symbols}

        # dynamic underlying state
        self.last_price = {s: None for s in orchestrator.symbols}

    # ------------------------------------------------------------------
    # OCC Encoding
    # ------------------------------------------------------------------
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        """
        symbol: "SPY"
        expiry: "2024-11-22"
        right: "C" or "P"
        strike: 450.0
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
        atm = round(price)
        return [atm - 2, atm - 1, atm, atm + 1, atm + 2]

    # ------------------------------------------------------------------
    # Main Trigger (called by Stocks WS adapter)
    # ------------------------------------------------------------------
    async def on_underlying(self, event: Dict):
        symbol = event.get("symbol")
        price = event.get("price")
        if symbol not in self.last_price:
            return

        self.last_price[symbol] = price

        # Build cluster
        expiry = self.orch.expiry_map.get(symbol)
        if not expiry:
            return  # weekly filter will sometimes skip

        strikes = self._compute_strikes(price)
        occ_codes = []

        # Calls and puts for each strike
        for K in strikes:
            occ_codes.append(self.encode_occ(symbol, expiry, "C", K))
            occ_codes.append(self.encode_occ(symbol, expiry, "P", K))

        # Deduplicate (could be same after rounding)
        occ_codes = sorted(list(set(occ_codes)))

        # Compare to existing subs
        current = self.current_subs.get(symbol, [])
        if set(current) == set(occ_codes):
            return  # no change

        logger.info(f"[CONTRACT_ENGINE] Refreshing {symbol} → {len(occ_codes)} contracts")

        # Update storage
        self.current_subs[symbol] = occ_codes

        # Send subscription
        await self.opt_ws.subscribe_contracts(occ_codes)

    # ------------------------------------------------------------------
    async def resubscribe_all(self):
        """Called after options_ws reconnect."""
        for symbol, occ_list in self.current_subs.items():
            if occ_list:
                await self.opt_ws.subscribe_contracts(occ_list)

