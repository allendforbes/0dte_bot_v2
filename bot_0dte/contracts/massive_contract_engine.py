"""
MassiveContractEngine v3.2 — A2-M, Fully aligned with Adapter v4.1 + MassiveMux v3.0
------------------------------------------------------------------------------------
Key properties:
  • Produces explicit OCC contract list for 0DTE (ATM ±1, ±2)
  • Supports MassiveMux boot-time OCC generation (build_occ_list_for_symbol)
  • Uses ws.set_occ_subscriptions() (no wildcards)
  • Handles expiry roll, stagnation refresh, convexity widening
  • Dynamic refresh triggered by underlying movement
"""

import asyncio
import time
import logging
from typing import List, Dict, Optional

from bot_0dte.universe import get_expiry_for_symbol

logger = logging.getLogger(__name__)


class MassiveContractEngine:
    # ============================================================
    # STRIKE RULES
    # ============================================================
    STRIKE_INCREMENTS = {
        "SPY": 1, "QQQ": 1,
        "TSLA": 1, "AAPL": 1, "AMZN": 1, "META": 1,
        "MSFT": 1, "NVDA": 5,
    }

    # Convexity widening earlier for these symbols
    MATMAN = {"META", "AAPL", "AMZN", "MSFT", "NVDA", "TSLA"}
    MATMAN_CONVEXITY_MULT = 0.50       # widen cluster earlier for MATMAN
    STAGNATION_REFRESH_SEC = 120.0     # force refresh if no price movement

    # ============================================================
    def __init__(self, symbol: str, ws):
        self.symbol = symbol.upper()
        self.ws = ws

        self.expiry: str = get_expiry_for_symbol(self.symbol)
        self._last_expiry_check = time.time()

        self.last_price: Optional[float] = None

        # active subscriptions: symbol → [occ_codes]
        self.current_subs: Dict[str, List[str]] = {}

        self._initialized = False
        self._lock = asyncio.Lock()

        self._last_refresh_ts = 0.0
        self._min_refresh_interval = 5.0

        self._last_price_change_ts = time.time()

    # ============================================================
    @property
    def contracts(self) -> List[str]:
        """Current active PURE OCC codes."""
        return self.current_subs.get(self.symbol, [])

    # ============================================================
    # OCC encoding
    # ============================================================
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        yyyy, mm, dd = expiry.split("-")
        yymmdd = f"{yyyy[2:]}{mm}{dd}"
        strike_thou = int(round(strike * 1000))
        return f"{symbol}{yymmdd}{right}{strike_thou:08d}"

    # ============================================================
    # Expiry roll
    # ============================================================
    def _check_expiry_roll(self) -> bool:
        now = time.time()
        if now - self._last_expiry_check < 60:
            return False

        self._last_expiry_check = now
        new_expiry = get_expiry_for_symbol(self.symbol)
        if new_expiry != self.expiry:
            logger.info(f"[OCC_EXPIRY_ROLL] {self.symbol} {self.expiry} → {new_expiry}")
            self.expiry = new_expiry
            return True
        return False

    # ============================================================
    # Strike computation (ATM ±1, and ±2 when convexity triggered)
    # ============================================================
    def _compute_strikes(self, price: float) -> List[float]:
        inc = self.STRIKE_INCREMENTS.get(self.symbol, 1)

        atm = int(round(price / inc)) * inc
        base = [atm - inc, atm, atm + inc]

        convexity_mult = (
            self.MATMAN_CONVEXITY_MULT if self.symbol in self.MATMAN else 0.75
        )

        # widen to ±2 if movement since last tick exceeds threshold
        if self.last_price and abs(price - self.last_price) >= convexity_mult * inc:
            base.extend([atm - 2 * inc, atm + 2 * inc])

        return sorted(set(base))

    # ============================================================
    def _current_center(self) -> Optional[float]:
        subs = self.current_subs.get(self.symbol, [])
        if not subs:
            return None

        strikes = []
        for occ in subs:
            try:
                strikes.append(int(occ[-8:]) / 1000.0)
            except Exception:
                pass

        if not strikes:
            return None

        strikes.sort()
        return strikes[len(strikes) // 2]

    # ============================================================
    # 🔥 NEW — Required by MassiveMux v3.0
    # Boot-time builder of OCC list
    # ============================================================
    async def build_occ_list_for_symbol(
        self, symbol: str, expiry: str, inc_strikes: int = 1
    ) -> List[str]:
        """
        Called *before* WS connect to generate initial OCC list.
        If price unknown, uses a safe ATM placeholder so WS can start.
        """
        # Try to harvest price from orchestrator (adapter backref)
        price = None

        if hasattr(self.ws, "parent_orchestrator"):
            orch = self.ws.parent_orchestrator
            if orch and symbol in orch.last_price:
                price = orch.last_price[symbol]

        # Fallback ATM center if price not known yet
        if price is None:
            price = 500 if symbol == "SPY" else 400  # safe placeholder

        inc = self.STRIKE_INCREMENTS.get(symbol, inc_strikes)
        atm = int(round(price / inc)) * inc

        strikes = [
            atm - inc, atm, atm + inc,
            atm - 2*inc, atm + 2*inc,
        ]

        occs = [
            self.encode_occ(symbol, expiry, side, k)
            for k in sorted(set(strikes))
            for side in ("C", "P")
        ]

        # store for reference so refresh logic works after WS comes up
        self.current_subs[symbol] = occs

        return occs

    # ============================================================
    async def on_underlying(self, event: dict):
        """Called continuously by MassiveMux."""
        if event.get("symbol") != self.symbol:
            return

        price = event.get("price")
        if price is None:
            return

        async with self._lock:
            now = time.monotonic()

            # detect movement
            if self.last_price is None or price != self.last_price:
                self._last_price_change_ts = time.time()

            rolled = self._check_expiry_roll()
            self.last_price = price

            # First-time init — subscribe OCC window
            if not self._initialized:
                await self._initialize(price)
                return

            # Forced refresh on stagnation
            if time.time() - self._last_price_change_ts >= self.STAGNATION_REFRESH_SEC:
                logger.info(f"[OCC_STAGNATION] {self.symbol} idle → forced refresh")
                await self._refresh(price)
                return

            # Determine if we need a new strike center
            old_center = self._current_center()
            new_center = round(price)

            need = (
                old_center is None
                or round(old_center) != new_center
                or rolled
            )

            if need and (now - self._last_refresh_ts >= self._min_refresh_interval):
                await self._refresh(price)

    # ============================================================
    async def _initialize(self, price: float):
        """Initial OCC subscription window."""
        if not self.expiry:
            logger.error(f"[OCC_INIT] No expiry for {self.symbol}")
            return

        strikes = self._compute_strikes(price)
        occs = [
            self.encode_occ(self.symbol, self.expiry, side, k)
            for k in strikes
            for side in ("C", "P")
        ]

        self.current_subs[self.symbol] = occs
        self._initialized = True
        self._last_refresh_ts = time.monotonic()

        logger.info(f"[OCC_INIT] {self.symbol} strikes={strikes} subs={len(occs)}")

        # NEW API — adapter v4.1
        await self.ws.set_occ_subscriptions(occs)

    # ============================================================
    async def _refresh(self, price: float):
        """Dynamic OCC refresh when underlying moves."""
        strikes = self._compute_strikes(price)

        occs = [
            self.encode_occ(self.symbol, self.expiry, side, k)
            for k in strikes
            for side in ("C", "P")
        ]

        if occs != self.current_subs.get(self.symbol, []):
            self.current_subs[self.symbol] = occs
            self._last_refresh_ts = time.monotonic()

            logger.info(
                f"[OCC_REFRESH] {self.symbol} center={price:.2f} "
                f"strikes={strikes} subs={len(occs)}"
            )

            self.ws.set_occ_subscriptions(occs)

    # ============================================================
    async def resubscribe_all(self):
        """Called after WS reconnect — must re-send exact OCC topics."""
        subs = self.current_subs.get(self.symbol, [])
        if subs:
            logger.info(f"[OCC_RESUB] {self.symbol} → {len(subs)}")
            self.ws.set_occ_subscriptions(occs)

