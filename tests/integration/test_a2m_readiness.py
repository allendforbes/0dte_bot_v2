# tests/integration/test_a2m_readiness.py

import time
import pytest
import asyncio

from bot_0dte.universe import (
    max_premium,
    max_latency_ms,
    get_expiry_for_symbol,
)

from bot_0dte.chain.chain_aggregator import ChainAggregator
from bot_0dte.strategy.strike_selector import StrikeSelector
from bot_0dte.strategy.elite_latency_precheck import EliteLatencyPrecheck

# FIXED PATH (correct location of MassiveContractEngine)
from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine


# -----------------------------------------------------------
# Premium ceiling tests
# -----------------------------------------------------------
def test_premium_ceiling_rules():
    # CORE — hard $1
    assert max_premium("SPY") == 1.00
    assert max_premium("QQQ") == 1.00

    # MATMAN — either 1.25 or 1.50 depending on weekday
    assert max_premium("AAPL") in (1.25, 1.50)
    assert max_premium("NVDA") in (1.25, 1.50)


# -----------------------------------------------------------
# Chain freshness tests
# -----------------------------------------------------------
def test_chain_snapshot_freshness():
    agg = ChainAggregator(["SPY"])

    # The snapshot should exist even before updates
    snap = agg.get("SPY")
    assert snap is not None

    now_ms = time.time() * 1000
    assert not snap.is_fresh(now_ms, 2000)  # chain is stale until NBBO arrives



# -----------------------------------------------------------
# StrikeSelector scoring tests
# -----------------------------------------------------------
@pytest.mark.asyncio
async def test_strike_selector_scoring():
    selector = StrikeSelector()

    rows = [
    {
        "symbol": "SPY",
        "strike": 440,
        "right": "C",
        "bid": 0.50,
        "ask": 0.52,
        "delta": 0.20,
        "gamma": 0.03,
        "_recv_ts": time.time(),
        "contract": "SPYTEST1",
    },
    {
        "symbol": "SPY",
        "strike": 441,
        "right": "C",
        "bid": 0.48,
        "ask": 0.50,
        "delta": 0.29,
        "gamma": 0.05,
        "_recv_ts": time.time(),
        "contract": "SPYTEST2",
    },
]


    best = await selector.select_from_chain(rows, "CALL", 440.3)
    assert best is not None
    assert best["strike"] == 440


# -----------------------------------------------------------
# Latency precheck tests
# -----------------------------------------------------------
def test_latency_precheck_allows_good_case():
    pre = EliteLatencyPrecheck()

    tick = {
        "price": 0.50,
        "bid": 0.49,
        "ask": 0.51,
        "delta": 0.29,
        "gamma": 0.02,
        "vwap_dev_change": 0.01,
        "_chain_age_ms": 20,
        "latency_ms": 20,
    }

    snap = {"upvol_pct": 60, "iv_change": 0.0}

    res = pre.validate("SPY", tick, "CALL", "A", snap)
    assert res.ok


def test_latency_precheck_blocks_above_ceiling():
    pre = EliteLatencyPrecheck()

    tick = {"price": 1.20, "bid": 1.18, "ask": 1.22}
    snap = {}

    res = pre.validate("SPY", tick, "CALL", "A", snap)
    assert not res.ok
    assert res.reason == "premium_ceiling"


# -----------------------------------------------------------
# Massive D2 widening tests
# -----------------------------------------------------------
@pytest.mark.asyncio
async def test_massive_d2_widening():
    class DummyWS:
        async def subscribe_contracts(self, contracts):
            pass

    eng = MassiveContractEngine("SPY", DummyWS())

    # INITIALIZE
    await eng._initialize(450)
    base = eng._compute_strikes(450)
    assert len(base) >= 3  # ATM ±1 always

    # Trigger convexity widening
    eng.last_price = 450
    widened = eng._compute_strikes(451)
    assert len(widened) >= 5  # A2-M widening condition
