"""
STRICT Convexity StrikeSelector (0DTE-only, MarketData.app-first)

Hierarchy:
    1. Use MarketData.app chain if available + fresh
    2. Fallback to IBKRChainBridge only when needed
    3. Enforce premium <= $1.00
    4. Only ATM / ATM±1 / ATM±2
    5. Reject weeklies Mon–Wed
"""

import pandas as pd
import numpy as np
from datetime import datetime

CORE = {"SPY", "QQQ"}
WEEKLIES = {"TSLA", "NVDA", "AAPL", "AMZN", "MSFT"}


class StrikeSelector:
    PREMIUM_CEILING = 1.00

    def __init__(self, chain_bridge, engine):
        """
        chain_bridge: IBKRChainBridge instance (fallback)
        engine: ExecutionEngine (provides md_chain_cache + last_price)
        """
        self.bridge = chain_bridge
        self.engine = engine

    # ---------------------------------------------------------
    @staticmethod
    def _today_exp():
        return datetime.now().strftime("%Y%m%d")

    # ---------------------------------------------------------
    @staticmethod
    def _allow_weeklies_today():
        # Thu = 3, Fri = 4
        return datetime.now().weekday() >= 3

    # ---------------------------------------------------------
    async def _load_chain(self, symbol: str, expiry: str):
        """
        Option C hierarchy:
        1) Use MarketDataFeed chain from orchestrator (zero-latency)
        2) Fallback to IBKRChainBridge
        """

        md_cache = self.engine.md_chain_cache.get(symbol)

        # MarketData.app chain present and non-empty
        if md_cache and isinstance(md_cache, list) and len(md_cache) > 0:
            return md_cache

        # Fallback to IBKR
        try:
            return await self.bridge.fetch_chain(symbol, expiry)
        except Exception:
            return []

    # ---------------------------------------------------------
    async def select(self, symbol: str, bias: str) -> dict:
        expiry = self.engine.expiry_map[symbol]

        # --------------------- 1) Weekly suppression ---------------------
        if symbol in WEEKLIES and not self._allow_weeklies_today():
            print(f"[PREMIUM GUARD] Rejecting {symbol} — weeklies blocked Mon–Wed.")
            return {}

        # --------------------- 2) Load chain -----------------------------
        chain = await self._load_chain(symbol, expiry)
        if not chain:
            print(f"[WARN] Empty chain for {symbol}")
            return {}

        df = pd.DataFrame(chain)
        df = df.replace({np.nan: None})

        # Numeric enforcement
        for col in ("strike", "bid", "ask", "last"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(subset=["strike"], inplace=True)

        today = self._today_exp()
        df = df[df["expiry"].astype(str) == today]
        if df.empty:
            print(f"[WARN] No today expiry for {symbol}")
            return {}

        # --------------------- 3) ATM determination ----------------------
        last = self.engine.last_price.get(symbol)
        if last is None:
            print(f"[WARN] No last price for {symbol}, cannot compute ATM")
            return {}

        strikes = sorted(df["strike"].unique())
        atm = min(strikes, key=lambda x: abs(x - last))

        candidates = [atm]
        for k in (1, 2):
            if atm - k in strikes:
                candidates.append(atm - k)
            if atm + k in strikes:
                candidates.append(atm + k)

        # --------------------- 4) Call/Put filter ------------------------
        right = "C" if bias == "CALL" else "P"
        df = df[df["right"].str.upper() == right]

        if df.empty:
            print(f"[WARN] No {bias} contracts for {symbol}")
            return {}

        # --------------------- 5) Premium convexity rule -----------------
        best_pick = None
        best_dist = float("inf")

        for K in candidates:
            sub = df[df["strike"] == K]
            if sub.empty:
                continue

            sub = sub.assign(mid=(sub["bid"] + sub["ask"]) / 2)
            sub = sub.dropna(subset=["mid"])
            if sub.empty:
                continue

            # Pick mid <= 1.00, closest to 1.00 (highest convexity)
            sub = sub[sub["mid"] <= self.PREMIUM_CEILING]
            if sub.empty:
                continue

            # Distance from 1.00 (we want mid near $1 convexity line)
            sub["dist"] = (sub["mid"] - self.PREMIUM_CEILING).abs()

            row = sub.sort_values("dist").iloc[0]
            if row["dist"] < best_dist:
                best_dist = row["dist"]
                best_pick = row

        if best_pick is None:
            print(f"[PREMIUM GUARD] {symbol}: all ATM±2 > ${self.PREMIUM_CEILING}.")
            return {}

        return {
            "symbol": symbol,
            "right": right,
            "strike": float(best_pick["strike"]),
            "expiry": today,
            "premium": round(float(best_pick["mid"]), 2),
            "bid": float(best_pick["bid"]),
            "ask": float(best_pick["ask"]),
        }
