"""
MassiveContractEngine v4.0 — Fully Hydrated (NBBO + REST Snapshot)
------------------------------------------------------------------
Adds:
  • MassiveSnapshotClient integration
  • Hydration loop for Greeks, IV, OI, volume
  • Merges REST data into ChainAggregator rows
  • Works with NBBO + MassiveMux 3.5
"""

import asyncio
import time
import logging
from typing import List, Dict, Optional

from bot_0dte.universe import get_expiry_for_symbol
from bot_0dte.data.providers.massive.massive_rest_snapshot_client import MassiveSnapshotClient

logger = logging.getLogger(__name__)


class MassiveContractEngine:

    STRIKE_INCREMENTS = {
        "SPY": 1, "QQQ": 1,
        "TSLA": 1, "AAPL": 1, "AMZN": 1, "META": 1,
        "MSFT": 1, "NVDA": 5,
    }

    MATMAN = {"META", "AAPL", "AMZN", "MSFT", "NVDA", "TSLA"}
    MATMAN_CONVEXITY_MULT = 0.50
    STAGNATION_REFRESH_SEC = 120.0

    HYDRATION_INTERVAL = 0.75   # snapshot loop
    MAX_CONTRACTS_PER_TICK = 6 # throttle snapshot bursts

    # ----------------------------------------------------------------------
    def __init__(self, symbol: str, ws):
        self.symbol = symbol.upper()
        self.ws = ws

        self.expiry: str = get_expiry_for_symbol(self.symbol)
        self._last_expiry_check = time.time()

        self.last_price: Optional[float] = None
        self._last_price_change_ts = time.time()
        self._initialized = False
        self._last_refresh_ts = 0
        self._min_refresh_interval = 5.0

        # occ subscriptions
        self.current_subs: Dict[str, List[str]] = {}

        # NEW — snapshot hydration client
        api_key = getattr(ws, "api_key", None) or getattr(ws, "apiKey", None)
        if not api_key:
            raise RuntimeError("MassiveContractEngine: Missing MASSIVE_API_KEY in options_ws")

        self.snapshot = MassiveSnapshotClient(api_key)

        # snapshot loop
        self._hydration_task = None
        self._running = False

        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------------
    @property
    def contracts(self) -> List[str]:
        return self.current_subs.get(self.symbol, [])

    # ----------------------------------------------------------------------
    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        yyyy, mm, dd = expiry.split("-")
        yymmdd = f"{yyyy[2:]}{mm}{dd}"
        strike_thou = int(round(strike * 1000))
        return f"{symbol}{yymmdd}{right}{strike_thou:08d}"

    # ----------------------------------------------------------------------
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

    # ----------------------------------------------------------------------
    def _compute_strikes(self, price: float) -> List[float]:
        inc = self.STRIKE_INCREMENTS.get(self.symbol, 1)
        atm = int(round(price / inc)) * inc
        base = [atm - inc, atm, atm + inc]

        convex_mult = (
            self.MATMAN_CONVEXITY_MULT if self.symbol in self.MATMAN else 0.75
        )

        if self.last_price and abs(price - self.last_price) >= convex_mult * inc:
            base.extend([atm - 2 * inc, atm + 2 * inc])

        return sorted(set(base))

    # ----------------------------------------------------------------------
    def _current_center(self) -> Optional[float]:
        subs = self.current_subs.get(self.symbol, [])
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

    # ----------------------------------------------------------------------
    async def build_occ_list_for_symbol(self, symbol: str, expiry: str, inc_strikes=1) -> List[str]:
        price = None

        if hasattr(self.ws, "parent_orchestrator"):
            orch = self.ws.parent_orchestrator
            if orch and symbol in orch.last_price:
                price = orch.last_price[symbol]

        if price is None:
            price = 500 if symbol == "SPY" else 400

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

        self.current_subs[symbol] = occs
        return occs

    # ----------------------------------------------------------------------
    async def on_underlying(self, event: dict):
        if event.get("symbol") != self.symbol:
            return

        price = event.get("price")
        if price is None:
            return

        async with self._lock:
            now = time.monotonic()

            if self.last_price is None or price != self.last_price:
                self._last_price_change_ts = time.time()

            rolled = self._check_expiry_roll()
            self.last_price = price

            if not self._initialized:
                await self._initialize(price)
                return

            # forced refresh on stagnation
            if time.time() - self._last_price_change_ts >= self.STAGNATION_REFRESH_SEC:
                logger.info(f"[OCC_STAGNATION] {self.symbol} forced refresh")
                await self._refresh(price)
                return

            old_center = self._current_center()
            new_center = round(price)

            need = (
                old_center is None
                or round(old_center) != new_center
                or rolled
            )

            if need and (now - self._last_refresh_ts >= self._min_refresh_interval):
                await self._refresh(price)

    # ----------------------------------------------------------------------
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

        logger.info(f"[OCC_INIT] {self.symbol} strikes={strikes} subs={len(occs)}")
        await self.ws.set_occ_subscriptions(occs)

        # 🔥 start hydration loop
        if not self._hydration_task:
            self._running = True
            self._hydration_task = asyncio.create_task(self._hydration_loop())

    # ----------------------------------------------------------------------
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
                f"[OCC_REFRESH] {self.symbol} center={price:.2f} "
                f"strikes={strikes} subs={len(occs)}"
            )

            await self.ws.set_occ_subscriptions(occs)

    # ----------------------------------------------------------------------
    # 🔥 HYDRATION LOOP — REST SNAPSHOT MERGE
    # ----------------------------------------------------------------------
    async def _hydration_loop(self):
        """Fetch IV + Greeks + Volume periodically for each contract cluster."""
        while self._running:
            try:
                occ_list = self.contracts
                if not occ_list:
                    await asyncio.sleep(self.HYDRATION_INTERVAL)
                    continue

                # throttle contracts per hydration tick
                batch = occ_list[: self.MAX_CONTRACTS_PER_TICK]

                for occ in batch:
                    snap = await self.snapshot.fetch_contract(
                        underlying=self.symbol,
                        occ=occ,
                    )
                    if not snap:
                        continue

                    # Inject hydration into ChainAggregator
                    orch = getattr(self.ws, "parent_orchestrator", None)
                    if orch:
                        chain = orch.chain_agg
                        row = chain.update_from_snapshot(self.symbol, occ, snap)
                        # no error if missing

                await asyncio.sleep(self.HYDRATION_INTERVAL)

            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(f"[HYDRATION] Error in hydration loop for {self.symbol}")

    # ----------------------------------------------------------------------
    async def stop(self):
        self._running = False
        if self._hydration_task:
            self._hydration_task.cancel()
            self._hydration_task = None
