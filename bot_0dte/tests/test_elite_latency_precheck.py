"""
Unit tests for EliteLatencyPrecheck v2.0

Tests cover:
    • Basic validation (passing case)
    • Missing/invalid prices
    • Locked markets
    • Stale NBBO
    • Wide spreads (grade-aware)
    • Slippage protection (grade-aware)
    • Micro reversals
    • Thin liquidity
    • Mid drift
    • Limit price construction
"""

import pytest
from bot_0dte.strategy.elite_latency_precheck import (
    EliteLatencyPrecheck,
    PrecheckResult,
)


class TestBasicValidation:
    """Test basic passing cases."""

    def test_call_a_grade_passes(self):
        """CALL A-grade with good conditions passes."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,  # Positive momentum
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is True
        assert result.limit_price == 1.05  # CALL → ask
        assert result.reason is None

    def test_put_a_plus_grade_passes(self):
        """PUT A+ grade with good conditions passes."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": -0.02,  # Negative momentum
            "_chain_age": 0.5,
        }

        result = pc.validate("QQQ", tick, "PUT", "A+")
        assert result.ok is True
        assert result.limit_price == 0.95  # PUT → bid
        assert result.reason is None


class TestMissingInvalidPrices:
    """Test rejection for missing or invalid price data."""

    def test_missing_price(self):
        """Reject if price missing."""
        pc = EliteLatencyPrecheck()
        tick = {
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "missing_prices"

    def test_missing_bid(self):
        """Reject if bid missing."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "missing_prices"

    def test_missing_ask(self):
        """Reject if ask missing."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "bid_size": 10,
            "ask_size": 10,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "missing_prices"

    def test_zero_bid(self):
        """Reject if bid is zero."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.0,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "invalid_quotes"

    def test_negative_ask(self):
        """Reject if ask is negative."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": -1.05,
            "bid_size": 10,
            "ask_size": 10,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "invalid_quotes"


class TestLockedMarket:
    """Test rejection for locked/crossed markets."""

    def test_locked_market_bid_equals_ask(self):
        """Reject if bid == ask."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 1.00,
            "ask": 1.00,
            "bid_size": 10,
            "ask_size": 10,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "locked_market"

    def test_crossed_market_bid_gt_ask(self):
        """Reject if bid > ask."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 1.10,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "locked_market"


class TestStaleNBBO:
    """Test rejection for stale option data."""

    def test_stale_chain_rejects(self):
        """Reject if chain_age > 2.0 seconds."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 2.5,  # Too old
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "stale_nbbo"

    def test_exactly_at_threshold(self):
        """Pass if exactly at 2.0 seconds."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 2.0,  # Exactly at threshold
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is True

    def test_missing_chain_age_defaults_to_zero(self):
        """Missing chain_age defaults to 0.0 (passes)."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            # No _chain_age field
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is True


class TestSpreadSanity:
    """Test spread checks (grade-aware)."""

    def test_a_grade_wide_spread_rejects(self):
        """A-grade rejects if spread > 20%."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.85,
            "ask": 1.15,  # Spread = 0.30, mid = 1.00, spread_pct = 30%
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "wide_spread"

    def test_a_plus_tolerates_wider_spread(self):
        """A+ grade tolerates spread up to 30%."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.85,
            "ask": 1.15,  # Spread = 0.30, spread_pct = 30%
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A+")
        assert result.ok is True  # A+ allows up to 30%

    def test_a_plus_rejects_extreme_spread(self):
        """A+ rejects if spread > 30%."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.80,
            "ask": 1.20,  # Spread = 0.40, spread_pct = 40%
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A+")
        assert result.ok is False
        assert result.reason == "wide_spread"


class TestSlippageProtection:
    """Test slippage checks (grade-aware)."""

    def test_call_a_grade_excessive_slippage(self):
        """CALL A-grade rejects if ask too far from price (>12%)."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.15,  # Slippage = (1.15 - 1.00) / 1.00 = 15%
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "slippage_risk"

    def test_call_a_plus_tolerates_higher_slippage(self):
        """CALL A+ tolerates slippage up to 18%."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.15,  # Slippage = 15%
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A+")
        assert result.ok is True  # A+ allows up to 18%

    def test_put_a_grade_excessive_slippage(self):
        """PUT A-grade rejects if bid too far from price (>12%)."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.85,  # Slippage = (1.00 - 0.85) / 1.00 = 15%
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": -0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "PUT", "A")
        assert result.ok is False
        assert result.reason == "slippage_risk"


class TestMicroReversal:
    """Test jump-diffusion reversal detection."""

    def test_call_downward_momentum_rejects(self):
        """CALL rejects if slope < -0.01."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": -0.015,  # Downward momentum
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "micro_reversal"

    def test_put_upward_momentum_rejects(self):
        """PUT rejects if slope > 0.01."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.015,  # Upward momentum
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "PUT", "A")
        assert result.ok is False
        assert result.reason == "micro_reversal"

    def test_call_slight_negative_slope_passes(self):
        """CALL passes if slope slightly negative but above threshold."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": -0.005,  # Barely negative (above -0.01)
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is True


class TestLiquidity:
    """Test option liquidity checks."""

    def test_thin_bid_size_rejects(self):
        """Reject if bid_size < 5."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 3,  # Too thin
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "thin_liquidity"

    def test_thin_ask_size_rejects(self):
        """Reject if ask_size < 5."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 2,  # Too thin
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "thin_liquidity"

    def test_missing_size_passes(self):
        """Pass if size fields are None (unknown)."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": None,
            "ask_size": None,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is True


class TestMidDrift:
    """Test mid drift (trade-through guard)."""

    def test_excessive_mid_drift_rejects(self):
        """Reject if mid drifts > 10% from theoretical price."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 1.15,  # Mid = 1.20
            "ask": 1.25,  # Drift = (1.20 - 1.00) / 1.00 = 20%
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is False
        assert result.reason == "mid_drift"

    def test_acceptable_mid_drift_passes(self):
        """Pass if mid drift within 10%."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.97,  # Mid = 1.02
            "ask": 1.07,  # Drift = 2%
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is True


class TestLimitPriceConstruction:
    """Test marketable limit price construction."""

    def test_call_limit_price_is_ask(self):
        """CALL limit price should be ask."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": 0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "CALL", "A")
        assert result.ok is True
        assert result.limit_price == 1.05  # ask

    def test_put_limit_price_is_bid(self):
        """PUT limit price should be bid."""
        pc = EliteLatencyPrecheck()
        tick = {
            "price": 1.00,
            "bid": 0.95,
            "ask": 1.05,
            "bid_size": 10,
            "ask_size": 10,
            "vwap_dev_change": -0.02,
            "_chain_age": 0.5,
        }

        result = pc.validate("SPY", tick, "PUT", "A")
        assert result.ok is True
        assert result.limit_price == 0.95  # bid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
