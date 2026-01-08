"""
MassiveContractEngine v4.1 — Fully Hydrated (NBBO + REST Snapshot) (REFACTORED)
------------------------------------------------------------------
Manages OCC contract subscriptions and hydration for options data.

Features:
  • Dynamic strike subscription based on underlying price
  • MassiveSnapshotClient integration for Greeks/IV/OI
  • Hydration loop for periodic data refresh
  • Expiry roll detection
  • Stagnation refresh

REFACTORED:
  • Enhanced logging
  • Better error handling
  • Cleaner state management
"""

import asyncio
import time
import logging
import math
from typing import List, Dict, Optional, Any

from bot_0dte.universe import get_expiry_for_symbol
from bot_0dte.data.providers.massive.massive_rest_snapshot_client import MassiveSnapshotClient

logger = logging.getLogger(__name__)


class MassiveContractEngine:
    """
    Manages OCC contract subscriptions for a single underlying symbol.
    
    Responsibilities:
        - Build OCC contract list based on underlying price
        - Subscribe/unsubscribe to option contracts via WebSocket
        - Hydrate contracts with REST snapshot data (Greeks, IV, OI)
        - Handle expiry rolls
        - Refresh on price stagnation
    """

    # Strike increments by symbol
    STRIKE_INCREMENTS = {
        "SPY": 1, "QQQ": 1,
        "TSLA": 1, "AAPL": 1, "AMZN": 1, "META": 1,
        "MSFT": 1, "NVDA": 5,
    }

    # MATMAN symbols (higher premium, lower convexity multiplier)
    MATMAN = {"META", "AAPL", "AMZN", "MSFT", "NVDA", "TSLA"}
    MATMAN_CONVEXITY_MULT = 0.50
    DEFAULT_CONVEXITY_MULT = 0.75
    
    # Timing
    STAGNATION_REFRESH_SEC = 120.0
    EXPIRY_CHECK_INTERVAL_SEC = 60.0
    MIN_REFRESH_INTERVAL_SEC = 5.0
    
    # Hydration
    HYDRATION_INTERVAL_SEC = 0.75
    MAX_CONTRACTS_PER_TICK = 6

    def __init__(self, symbol: str, ws, log_func=None):
        """
        Initialize contract engine for symbol.
        
        Args:
            symbol: Underlying symbol (e.g., "SPY")
            ws: WebSocket connection with set_occ_subscriptions method
            log_func: Optional logging function
        """
        self.symbol = symbol.upper()
        self.ws = ws
        self._log_func = log_func or (lambda msg: logger.info(msg))

        # Expiry tracking
        self.expiry: str = get_expiry_for_symbol(self.symbol)
        self._last_expiry_check = time.time()

        # Price tracking
        self.last_price: Optional[float] = None
        self._last_price_change_ts = time.time()
        self._initialized = False
        self._last_refresh_ts = 0.0

        # OCC subscriptions
        self.current_subs: Dict[str, List[str]] = {}

        # Snapshot client for hydration
        api_key = getattr(ws, "api_key", None) or getattr(ws, "apiKey", None)
        if not api_key:
            raise RuntimeError(
                f"MassiveContractEngine: Missing MASSIVE_API_KEY for {self.symbol}"
            )

        self.snapshot = MassiveSnapshotClient(api_key)

        # Hydration loop
        self._hydration_task: Optional[asyncio.Task] = None
        self._running = False

        # Lock for state updates
        self._lock = asyncio.Lock()

    def _log(self, msg: str):
        self._log_func(msg)

    @property
    def contracts(self) -> List[str]:
        """Get current OCC contract subscriptions."""
        return self.current_subs.get(self.symbol, [])

    @staticmethod
    def encode_occ(symbol: str, expiry: str, right: str, strike: float) -> str:
        """
        Encode OCC contract symbol.
        
        Format: SYMBOL + YYMMDD + C/P + STRIKE*1000 (8 digits)
        Example: SPY240105C00450000
        """
        # Handle YYYY-MM-DD format
        if "-" in expiry:
            yyyy, mm, dd = expiry.split("-")
            yymmdd = f"{yyyy[2:]}{mm}{dd}"
        else:
            yymmdd = expiry[2:] if len(expiry) == 8 else expiry
        
        strike_thou = int(round(strike * 1000))
        return f"{symbol}{yymmdd}{right}{strike_thou:08d}"

    def _check_expiry_roll(self) -> bool:
        """
        Check if expiry has rolled to new date.
        
        Returns:
            True if expiry changed, False otherwise
        """
        now = time.time()
        if now - self._last_expiry_check < self.EXPIRY_CHECK_INTERVAL_SEC:
            return False

        self._last_expiry_check = now
        new_expiry = get_expiry_for_symbol(self.symbol)

        if new_expiry != self.expiry:
            self._log(f"[OCC_EXPIRY_ROLL] {self.symbol} {self.expiry} → {new_expiry}")
            self.expiry = new_expiry
            return True
        return False

    def _compute_strikes(self, price: float) -> List[float]:
        """
        Compute strike prices for subscription.
        
        Returns ATM ± 1 base, expands to ATM ± 2 on significant moves.
        """
        inc = self.STRIKE_INCREMENTS.get(self.symbol, 1)
        atm = int(round(price / inc)) * inc
        base = [atm - inc, atm, atm + inc]

        # Expand on significant move
        convex_mult = (
            self.MATMAN_CONVEXITY_MULT if self.symbol in self.MATMAN 
            else self.DEFAULT_CONVEXITY_MULT
        )

        if self.last_price and abs(price - self.last_price) >= convex_mult * inc:
            base.extend([atm - 2 * inc, atm + 2 * inc])

        return sorted(set(base))

    def _current_center(self) -> Optional[float]:
        """Get center strike of current subscriptions."""
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

    async def build_occ_list_for_symbol(
        self, 
        symbol: str, 
        expiry: str, 
        inc_strikes: int = 1,
    ) -> List[str]:
        """
        Build OCC list for symbol (used for initial subscription).
        
        Args:
            symbol: Underlying symbol
            expiry: Expiry date (YYYY-MM-DD)
            inc_strikes: Strike increment override
        
        Returns:
            List of OCC contract symbols
        """
        price = None

        # Try to get price from orchestrator
        if hasattr(self.ws, "parent_orchestrator"):
            orch = self.ws.parent_orchestrator
            if orch and symbol in orch.last_price:
                price = orch.last_price[symbol]

        if price is None:
            # Fallback defaults
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

    async def on_underlying(self, event: Dict[str, Any]):
        """
        Handle underlying price update.
        
        Triggers subscription refresh when needed.
        """
        if event.get("symbol") != self.symbol:
            return

        price = event.get("price")
        if price is None:
            return

        async with self._lock:
            now = time.monotonic()

            # Track price changes
            if self.last_price is None or price != self.last_price:
                self._last_price_change_ts = time.time()

            rolled = self._check_expiry_roll()
            self.last_price = price

            # Initialize on first price
            if not self._initialized:
                await self._initialize(price)
                return

            # Forced refresh on stagnation
            if time.time() - self._last_price_change_ts >= self.STAGNATION_REFRESH_SEC:
                self._log(f"[OCC_STAGNATION] {self.symbol} forced refresh")
                await self._refresh(price)
                return

            # Check if refresh needed
            old_center = self._current_center()
            new_center = round(price)

            need_refresh = (
                old_center is None
                or round(old_center) != new_center
                or rolled
            )

            if need_refresh and (now - self._last_refresh_ts >= self.MIN_REFRESH_INTERVAL_SEC):
                await self._refresh(price)

    async def _initialize(self, price: float):
        """Initialize subscriptions on first price."""
        if not self.expiry:
            self._log(f"[OCC_INIT] No expiry for {self.symbol}")
            return

        # Guard: invalid price
        if price is None or math.isnan(price):
            self._log(f"[OCC_INIT] Invalid price: {price}")
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

        self._log(f"[OCC_INIT] {self.symbol} strikes={strikes} subs={len(occs)}")
        await self.ws.set_occ_subscriptions(occs)

        # Start hydration loop
        if not self._hydration_task:
            self._running = True
            self._hydration_task = asyncio.create_task(self._hydration_loop())

    async def _refresh(self, price: float):
        """Refresh subscriptions based on new price."""
        # Guard: invalid price
        if price is None or math.isnan(price):
            return

        strikes = self._compute_strikes(price)

        occs = [
            self.encode_occ(self.symbol, self.expiry, side, k)
            for k in strikes
            for side in ("C", "P")
        ]

        if occs != self.current_subs.get(self.symbol, []):
            self.current_subs[self.symbol] = occs
            self._last_refresh_ts = time.monotonic()

            self._log(
                f"[OCC_REFRESH] {self.symbol} center={price:.2f} "
                f"strikes={strikes} subs={len(occs)}"
            )

            await self.ws.set_occ_subscriptions(occs)

    async def _hydration_loop(self):
        """
        Fetch IV + Greeks + Volume periodically for each contract.
        
        Runs continuously while engine is active.
        """
        while self._running:
            try:
                occ_list = self.contracts
                if not occ_list:
                    await asyncio.sleep(self.HYDRATION_INTERVAL_SEC)
                    continue

                # Throttle contracts per hydration tick
                batch = occ_list[: self.MAX_CONTRACTS_PER_TICK]

                for occ in batch:
                    try:
                        snap = await self.snapshot.fetch_contract(
                            underlying=self.symbol,
                            occ=occ,
                        )
                        if not snap:
                            continue

                        # Inject hydration into ChainAggregator
                        orch = getattr(self.ws, "parent_orchestrator", None)
                        if orch and hasattr(orch, "chain_agg"):
                            orch.chain_agg.update_from_snapshot(self.symbol, occ, snap)
                    except Exception as e:
                        self._log(f"[HYDRATION] Error fetching {occ}: {e}")

                await asyncio.sleep(self.HYDRATION_INTERVAL_SEC)

            except asyncio.CancelledError:
                return
            except Exception as e:
                self._log(f"[HYDRATION] Loop error for {self.symbol}: {e}")
                await asyncio.sleep(1.0)  # Back off on error

    async def stop(self):
        """Stop the engine and cleanup."""
        self._running = False
        if self._hydration_task:
            self._hydration_task.cancel()
            try:
                await self._hydration_task
            except asyncio.CancelledError:
                pass
            self._hydration_task = None
        self._log(f"[OCC_STOP] {self.symbol} stopped")
    
    def get_state(self) -> Dict[str, Any]:
        """Get current engine state for debugging."""
        return {
            "symbol": self.symbol,
            "expiry": self.expiry,
            "last_price": self.last_price,
            "initialized": self._initialized,
            "running": self._running,
            "contracts": len(self.contracts),
            "current_subs": self.contracts[:5] if self.contracts else [],  # First 5
        }