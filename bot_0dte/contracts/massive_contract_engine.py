"""
MassiveContractEngine v3.1 — Dynamic OCC Subscription Manager (D2 + A2-M)
------------------------------------------------------------------------
Enhancements in A2-M:
    • Daily expiry refresh (Massive requires correct listing date each morning)
    • Stronger D2 strike extension trigger using delta-sensitive movement
    • MATMAN convexity bias (earlier ±2 extension)
    • Structured logging
    • Safe refresh on feed stagnation
    • Zero API changes — selector and aggregator remain unaffected
"""

import asyncio
import time
import logging
from typing import List, Dict

from bot_0dte.universe import get_expiry_for_symbol

logger = logging.getLogger(__name__)


class MassiveContractEngine:
    """
    Maintains dynamic OCC subscription set per symbol.
    Sends PURE codes like: SPY251205C00684000
    """

    STRIKE_INCREMENTS = {
        "SPY": 1,
        "QQQ": 1,
        "TSLA": 1,
        "AAPL": 1,
        "AMZN": 1,
        "META": 1,
        "MSFT": 1,
        "NVDA": 5,
    }

    # ------------------------------------------------------------
    # A2-M PARAMETERS
    # ------------------------------------------------------------
    MATMAN = {"META", "AAPL", "AMZN", "MSFT", "NVDA", "TSLA"}

    # MATMAN tapers into convexity faster → widen cluster sooner
    MATMAN_CONVEXITY_MULT = 0.50   # vs 0.75 baseline

    # Refresh when underlying hasn't changed for long periods
    STAGNATION_REFRESH_SEC = 120.0

    # ------------------------------------------------------------
    def __init__(self, symbol: str, ws):
        self.symbol = symbol.upper()
        self.ws = ws

        self.expiry = get_expiry_for_symbol(self.symbol)
        self._last_expiry_check = time.time()

        self.last_price = None

        # active PURE OCC codes
        self.current_subs: Dict[str, List[str]] = {}

        # refresh controls
        self._last_refresh_ts = 0.0
        self._min_refresh_interval = 5.0
        self._initialized = False
        self._lock = asyncio.Lock()

        # track price stagnation
        self._last_price_change_ts = time.time()

    # ------------------------------------------------------------
    @property
    def contracts(self) -> List[str]:
        return self.current_subs.get(self.symbol, [])

    # ------------------------------------------------------------
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        yyyy, mm, dd = expiry.split("-")
        yymmdd = f"{yyyy[2:]}{mm}{dd}"
        strike_thou = int(round(strike * 1000))
        return f"{symbol}{yymmdd}{right}{strike_thou:08d}"

    # ------------------------------------------------------------
    ### A2-M: expiry rollover support
    def _check_expiry_roll(self):
        """Refresh expiry each morning or when universe rolls."""
        now = time.time()
        if now - self._last_expiry_check < 60.0:
            return

        self._last_expiry_check = now

        new_expiry = get_expiry_for_symbol(self.symbol)
        if new_expiry != self.expiry:
            logger.info(f"[OCC_EXPIRY_ROLL] {self.symbol} {self.expiry} → {new_expiry}")
            self.expiry = new_expiry
            # force refresh immediately
            return True

        return False

    # ------------------------------------------------------------
    def _compute_strikes(self, price: float) -> List[float]:
        """
        ATM ±1 baseline.
        ATM ±2 extension added when convexity risk is high.
        A2-M: MATMAN symbols widen earlier.
        """
        inc = self.STRIKE_INCREMENTS.get(self.symbol, 1)

        # ATM rounding
        atm = int(round(price / inc)) * inc

        base = [atm - inc, atm, atm + inc]

        # A2-M convexity trigger
        convexity_mult = (
            self.MATMAN_CONVEXITY_MULT if self.symbol in self.MATMAN else 0.75
        )

        if (
            self.last_price
            and abs(price - self.last_price) >= convexity_mult * inc
        ):
            base.extend([atm - 2 * inc, atm + 2 * inc])

        return sorted(set(base))

    # ------------------------------------------------------------
    def _current_center(self) -> float | None:
        subs = self.current_subs.get(self.symbol)
        if not subs:
            return None

        strikes = []
        for occ in subs:
            try:
                strikes.append(int(occ[-8:]) / 1000.0)
            except:
                pass

        if not strikes:
            return None

        strikes.sort()
        return strikes[len(strikes) // 2]

    # ------------------------------------------------------------
    async def on_underlying(self, event: dict):
        if event.get("symbol") != self.symbol:
            return

        price = event.get("price")
        if price is None:
            return

        async with self._lock:
            now = time.monotonic()

            # update last_price_change_ts
            if self.last_price is None or price != self.last_price:
                self._last_price_change_ts = time.time()

            # expiry rollover
            rolled = self._check_expiry_roll()

            self.last_price = price

            if not self._initialized:
                await self._initialize(price)
                return

            # stagnation refresh
            if time.time() - self._last_price_change_ts >= self.STAGNATION_REFRESH_SEC:
                logger.info(f"[OCC_STAGNATION] {self.symbol} price idle → forced refresh")
                await self._refresh(price)
                return

            # need refresh?
            new_center = round(price)
            old_center = self._current_center()

            if old_center is None:
                need = True
            else:
                need = (round(old_center) != new_center) or rolled

            if need and (now - self._last_refresh_ts >= self._min_refresh_interval):
                await self._refresh(price)

    # ------------------------------------------------------------
    async def _initialize(self, price: float):
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

        logger.info(
            f"[OCC_INIT] {self.symbol} inc={self.STRIKE_INCREMENTS[self.symbol]} "
            f"strikes={strikes} subs={len(occs)}"
        )
        await self.ws.subscribe_contracts(occs)

    # ------------------------------------------------------------
    async def _refresh(self, price: float):
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
                f"[OCC_REFRESH] {self.symbol} inc={self.STRIKE_INCREMENTS[self.symbol]} "
                f"center={price:.2f} strikes={strikes} subs={len(occs)}"
            )
            await self.ws.subscribe_contracts(occs)

    # ------------------------------------------------------------
    async def resubscribe_all(self):
        subs = self.current_subs.get(self.symbol, [])
        if subs:
            logger.info(f"[OCC_RESUB] {self.symbol} → {len(subs)} contracts")
            await self.ws.subscribe_contracts(subs)
