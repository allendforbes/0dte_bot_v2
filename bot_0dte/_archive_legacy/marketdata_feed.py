# MarketDataFeed.py (fully corrected)

import asyncio
import aiohttp
import time
from typing import Dict, Any, Callable, List, Optional


class MarketDataFeed:
    """
    REST-optimized MarketData.app feed.

    Correct endpoints:
        • /options/expirations/{symbol}
        • /options/quotes/{symbol}/{expiry}   ← per-expiry quotes
        • /options/chain/{symbol}             ← full chain, filter locally
    """

    BASE_URL = "https://api.marketdata.app/v1"

    # -----------------------------------------------------
    def __init__(
        self,
        callback: Optional[Callable] = None,
        api_key: str = "",
        api_token: str = "",
        interval: float = 1.5,
    ):
        self.callback = callback
        self.api_key = api_key or api_token
        self.interval = interval

        self._running = False

        # Bounded concurrency for API safety
        self.semaphore = asyncio.Semaphore(5)

    # =====================================================
    # CORRECT REST ENDPOINTS
    # =====================================================
    async def _fetch_quotes(self, session, symbol: str, expiry: str):
        """
        Fetch ALL option quotes for a single expiration.
        This replaces the WRONG snapshot endpoint.
        """
        if not expiry:
            return None

        url = f"{self.BASE_URL}/options/quotes/{symbol}/{expiry}?token={self.api_key}"

        try:
            async with session.get(url, timeout=3.0) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()  # returns a list of contracts
        except Exception:
            return None

    async def _fetch_chain(self, session, symbol: str, expiry: str):
        """
        Fetch full chain and filter to the needed expiration.
        Correct endpoint: /options/chain/{symbol}
        """
        if not expiry:
            return []

        url = f"{self.BASE_URL}/options/chain/{symbol}?token={self.api_key}"

        try:
            async with session.get(url, timeout=3.0) as resp:
                if resp.status != 200:
                    return []
                raw = await resp.json()
        except Exception:
            return []

        all_opts = raw.get("options", []) or []
        return [o for o in all_opts if o.get("expiration") == expiry]

    # =====================================================
    # PARALLEL TASK (quotes + chain)
    # =====================================================
    async def _fetch_tick(self, session, symbol: str, expiry: str):
        """
        Returns a unified tick:
            • price = underlyingPrice
            • bid/ask from ATM contract (highest volume)
            • vwap + greeks from contract snapshot
            • full chain list included
        """
        async with self.semaphore:
            quotes = await self._fetch_quotes(session, symbol, expiry)
            chain = await self._fetch_chain(session, symbol, expiry)

        # If no quotes available, deliver safe tick
        if not quotes:
            return {
                "symbol": symbol,
                "price": None,
                "bid": None,
                "ask": None,
                "vwap": None,
                "vwap_dev_change": None,
                "upvol_pct": None,
                "flow_ratio": None,
                "iv_change": None,
                "skew_shift": None,
                "chain": chain,
            }

        # choose ATM contract (highest volume)
        best = max(quotes, key=lambda q: q.get("volume", 0))

        price = best.get("underlyingPrice")

        return {
            "symbol": symbol,
            "price": price,
            "bid": best.get("bid"),
            "ask": best.get("ask"),
            "vwap": best.get("mid"),  # marketdata.app doesn't provide VWAP
            "vwap_dev_change": None,  # placeholder until model derives it
            "upvol_pct": None,
            "flow_ratio": None,
            "iv_change": None,
            "skew_shift": None,
            "chain": chain,
        }

    # =====================================================
    # START (REST ONLY)
    # =====================================================
    async def start(
        self, symbols: List[str], expiries: Optional[Dict[str, str]] = None
    ):
        if not self.callback:
            raise RuntimeError("MarketDataFeed missing callback")

        self._running = True
        print("[FEED] MarketDataFeed started in REST mode.")

        await self._poll_loop(symbols, expiries or {})

    # =====================================================
    # POLLING LOOP
    # =====================================================
    async def _poll_loop(self, symbols: List[str], expiries: Dict[str, str]):
        async with aiohttp.ClientSession() as session:
            while self._running:
                t0 = time.time()

                tasks = [
                    self._fetch_tick(session, sym, expiries.get(sym)) for sym in symbols
                ]

                for coro in asyncio.as_completed(tasks):
                    tick = await coro
                    try:
                        await self.callback(tick)
                    except Exception as e:
                        print(f"[FEED][WARN] Callback rejected: {e}")

                elapsed = time.time() - t0
                await asyncio.sleep(max(0, self.interval - elapsed))

    # =====================================================
    async def stop(self):
        self._running = False
