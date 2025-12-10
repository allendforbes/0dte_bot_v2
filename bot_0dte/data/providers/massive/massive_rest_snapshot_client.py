import aiohttp
import asyncio
import time


class MassiveSnapshotClient:
    """
    Async REST snapshot fetcher for options Greeks, IV, OI, volume.

    - Throttled: max ~5 req/sec (Massive safe limit)
    - Caches results for 500ms per contract
    """

    BASE_URL = "https://api.massive.app/v3/snapshot/options"
    CACHE_TTL = 0.50     # seconds per contract snapshot
    MAX_RPS = 5          # safe sustained rate

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._cache = {}       # contract → (ts, payload)
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.MAX_RPS)

    # --------------------------------------------------------------
    async def _rate_limited(self):
        """Provide rate limiting with semaphore + small spacing."""
        await self._semaphore.acquire()
        await asyncio.sleep(1 / self.MAX_RPS)
        self._semaphore.release()

    # --------------------------------------------------------------
    async def fetch_contract(self, underlying: str, occ: str) -> dict:
        """
        Fetch complete REST snapshot for a single contract:
            delta, gamma, theta, vega, iv, open_interest, volume
        """

        now = time.time()
        cached = self._cache.get(occ)
        if cached and (now - cached[0]) < self.CACHE_TTL:
            return cached[1]

        url = f"{self.BASE_URL}/{underlying}/{occ}"

        headers = {
            "accept": "application/json",
            "x-api-key": self.api_key,
        }

        async with self._lock:
            # another coroutine might have fetched during waiting
            cached = self._cache.get(occ)
            if cached and (time.time() - cached[0]) < self.CACHE_TTL:
                return cached[1]

            # rate limit
            await self._rate_limited()

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return {}

                    data = await resp.json()

                    # Massive wraps data under "data"
                    snap = data.get("data") or {}

                    payload = {
                        "delta": snap.get("delta"),
                        "gamma": snap.get("gamma"),
                        "theta": snap.get("theta"),
                        "vega": snap.get("vega"),
                        "iv": snap.get("iv"),
                        "open_interest": snap.get("open_interest") or snap.get("oi"),
                        "volume": snap.get("volume") or snap.get("vol"),
                    }

                    self._cache[occ] = (time.time(), payload)
                    return payload
