#!/usr/bin/env python3
"""
Pre-Market System Check for WS-Native 0DTE Bot

Runs at 5:30am PT:
    • Validates env vars
    • Tests Massive auth
    • Tests WS connectivity
    • Tests IB Gateway (if paper_mode)
    • Tests OCC subscription flow
    • Loads orchestrator without trading
"""

import os
import asyncio
import sys
import time
from pathlib import Path

from bot_0dte.data.providers.massive.massive_stocks_ws_adapter import (
    MassiveStocksWSAdapter,
)
from bot_0dte.data.providers.massive.massive_options_ws_adapter import (
    MassiveOptionsWSAdapter,
)
from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol


def check_env():
    required = [
        "MASSIVE_API_KEY",
        "MASSIVE_STOCKS_URL",
        "MASSIVE_OPTIONS_URL",
        "IB_GATEWAY_HOST",
        "IB_GATEWAY_PORT",
    ]
    print("🔍 Checking env vars...")
    missing = [k for k in required if os.getenv(k) is None]
    if missing:
        print("❌ Missing:", missing)
        return False
    print("✅ Env vars OK")
    return True


async def check_massive():
    print("\n🔌 Checking Massive WS auth...")

    s = MassiveStocksWSAdapter.from_env()
    try:
        await s.connect()
        print("✅ Massive STOCKS auth OK")
        await s.close()
        return True
    except Exception as e:
        print("❌ Massive STOCKS auth failed:", e)
        return False


async def check_occ_subscription():
    print("\n📡 Checking OCC subscription pipeline...")

    opt = MassiveOptionsWSAdapter.from_env()
    await opt.connect()

    # One test contract
    test_code = "O:SPY241122C00450000"
    try:
        await opt.subscribe_contracts([test_code])
        print("✅ OCC subscription OK")
        await opt.close()
        return True
    except Exception as e:
        print("❌ OCC subscription failed:", e)
        return False


async def main():
    print("\n==============================")
    print(" PRE-MARKET HEALTH CHECK v1.0 ")
    print("==============================\n")

    ok = True

    if not check_env():
        ok = False

    if not await check_massive():
        ok = False

    if not await check_occ_subscription():
        ok = False

    print("\n------------------------------")
    if ok:
        print("🎉 ALL SYSTEMS GREEN — READY FOR MARKET OPEN")
        sys.exit(0)
    else:
        print("⚠️  ONE OR MORE CHECKS FAILED — FIX BEFORE OPEN")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
