import asyncio
import aiohttp
import time
from typing import Dict, Any, Callable, List, Optional


class MarketDataFeed:
    """
    High-speed MarketData.app polling feed.

    Responsibilities:
        • Poll MarketData.app for each symbol
        • Normalize snapshots into flat tick dict
        • Push ticks → callback(symbol_tick)
        • Option chains fetched once per cycle (fast)
        • Zero IBKR data calls
    """

    BASE_URL = "https://api.marketdata.app/v1"

    def __init__(self, callback: Optional[Callable] = None,
                 api_key: str = "", interval: float = 1.5):
        self.callback = callback
        self.api_key = api_key
        self.interval = interval
        self._running = False

    # -------------------------------------------------------
    async def _fetch_snapshot(self, session, symbol: str, expiry: str):
        """Fetch underlying + top-of-book option snapshot."""
        url = f"{self.BASE_URL}/options/snapshot/{symbol}/{expiry}?token={self.api_key}"
        try:
            async with session.get(url, timeout=2.5) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception:
            return None

    # -------------------------------------------------------
    async def _fetch_chain(self, session, symbol: str, expiry: str):
        """Fetch entire option chain cheaply (premium-focused)."""
        url = f"{self.BASE_URL}/options/chain/{symbol}/{expiry}?token={self.api_key}"
        try:
            async with session.get(url, timeout=2.5) as resp:
                if resp.status != 200:
                    return []
                raw = await resp.json()
        except Exception:
            return []

        opts = raw.get("options", []) or []
        out = []

        for o in opts:
            try:
                out.append({
                    "symbol": symbol,
                    "expiry": o.get("expiration"),
                    "strike": float(o.get("strike")),
                    "right": o.get("type", "").upper(),
                    "bid": float(o.get("bid") or 0),
                    "ask": float(o.get("ask") or 0),
                    "last": float(o.get("last") or 0),
                    "iv": float(o.get("iv") or 0),
                })
            except Exception:
                continue
        return out

    # -------------------------------------------------------
    async def start(self, symbols: List[str], expiries: Dict[str, str]):
        """
        Feed pushes FLAT normalized ticks:
            {
              "symbol": "SPY",
              "price": ...,
              "bid": ...,
              "ask": ...,
              "vwap": ...,
              "flow_ratio": ...,
              ...
              "chain": [list of options]
            }
        """

        if not self.callback:
            raise RuntimeError("MarketDataFeed missing callback")

        self._running = True
        print("[FEED] MarketDataFeed started.")

        async with aiohttp.ClientSession() as session:
            while self._running:
                t0 = time.time()

                for sym in symbols:
                    expiry = expiries.get(sym)

                    snap = await self._fetch_snapshot(session, sym, expiry)

                    if not snap:
                        # fallback empty tick
                        chain = await self._fetch_chain(session, sym, expiry)
                        tick = {
                            "symbol": sym,
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
                    else:
                        opt = snap.get("option", {})
                        und = snap.get("underlying", {})

                        chain = await self._fetch_chain(session, sym, expiry)

                        tick = {
                            "symbol": sym,
                            "price": und.get("last"),
                            "bid": opt.get("bid"),
                            "ask": opt.get("ask"),
                            "vwap": und.get("vwap"),
                            "vwap_dev_change": und.get("vwap_dev_change"),
                            "upvol_pct": und.get("upvol_pct"),
                            "flow_ratio": und.get("flow_ratio"),
                            "iv_change": und.get("iv_change"),
                            "skew_shift": und.get("skew_shift"),
                            "chain": chain,
                        }

                    # Push tick → orchestrator
                    try:
                        await self.callback(tick)
                    except Exception as e:
                        print(f"[FEED][WARN] Callback rejected: {e}")

                # pacing
                elapsed = time.time() - t0
                await asyncio.sleep(max(0, self.interval - elapsed))

    # -------------------------------------------------------
    async def stop(self):
        self._running = False
