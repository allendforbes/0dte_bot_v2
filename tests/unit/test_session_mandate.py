"""
Test: SessionMandate Control Flow Verification

Verifies:
1. Permission is binary (ENTRY_ALLOWED allows, others block)
2. VWAP is context only (doesn't change state)
3. Early session uses same execution path (different metadata only)
4. Acceptance criteria work correctly

NOTE: Place this file in bot_0dte/tests/unit/test_session_mandate.py
      or adjust the import path below to match your project structure.
"""

import sys
import os

# Add project root to path for imports
# Adjust this path based on where you place the test file
# sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot_0dte.strategy.session_mandate import SessionMandateEngine, SessionMandate, RegimeState


def test_permission_is_binary():
    """Verify allows_entry() returns True only for ENTRY_ALLOWED."""
    
    # ENTRY_ALLOWED → True
    m1 = SessionMandate(
        state=RegimeState.ENTRY_ALLOWED,
        bias="CALL",
        regime_type="TREND",
        confidence=0.5,
        reason="test",
        reference_price=100.0,
    )
    assert m1.allows_entry() == True, "ENTRY_ALLOWED should allow entry"
    
    # SUPPRESSED → False
    m2 = SessionMandate(
        state=RegimeState.SUPPRESSED,
        bias="CALL",
        regime_type="TREND",
        confidence=0.5,
        reason="test",
        reference_price=100.0,
    )
    assert m2.allows_entry() == False, "SUPPRESSED should not allow entry"
    
    # NO_TRADE → False
    m3 = SessionMandate(
        state=RegimeState.NO_TRADE,
        bias=None,
        regime_type=None,
        confidence=0.0,
        reason="test",
        reference_price=None,
    )
    assert m3.allows_entry() == False, "NO_TRADE should not allow entry"
    
    print("✓ Permission is binary")


def test_vwap_is_context_only():
    """Verify VWAP affects metadata but not state."""
    
    engine = SessionMandateEngine()
    
    # Strong deviation
    snap1 = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,  # Strong positive
        "vwap_dev_change": 0.1,  # Aligned slope
        "seconds_since_open": 600,
    }
    
    # Weak deviation (same location, different magnitude)
    snap2 = {
        "price": 100.05,
        "vwap": 100.0,
        "vwap_dev": 0.05,  # Weak positive
        "vwap_dev_change": 0.01,  # Weak slope
        "seconds_since_open": 600,
    }
    
    m1 = engine.determine("SPY", snap1)
    engine._reset_acceptance_state("SPY")
    m2 = engine.determine("SPY", snap2)
    
    # Both should have same state (both initially SUPPRESSED, need hold bars)
    assert m1.state == m2.state, f"VWAP magnitude should not change state: {m1.state} vs {m2.state}"
    
    # Both should have same bias
    assert m1.bias == m2.bias == "CALL", "Both should detect CALL bias"
    
    # Confidence may differ (that's OK, it's metadata)
    print(f"  Confidence diff: {m1.confidence:.2f} vs {m2.confidence:.2f} (expected)")
    
    print("✓ VWAP is context only")


def test_early_session_same_path():
    """Verify early and late session use same execution path."""
    
    engine = SessionMandateEngine()
    
    # Early session
    snap_early = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": 0.1,
        "seconds_since_open": 60,  # 1 minute after open
    }
    
    # Late session (same market conditions)
    snap_late = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": 0.1,
        "seconds_since_open": 600,  # 10 minutes after open
    }
    
    m_early = engine.determine("SPY", snap_early)
    engine._reset_acceptance_state("SPY")
    m_late = engine.determine("SPY", snap_late)
    
    # Both should have same state (SUPPRESSED initially)
    assert m_early.state == m_late.state, f"Session time should not change state: {m_early.state} vs {m_late.state}"
    
    # Both should have same bias
    assert m_early.bias == m_late.bias == "CALL", "Both should detect CALL bias"
    
    # Confidence differs (early session has lower confidence)
    assert m_early.confidence < m_late.confidence, "Early session should have lower confidence"
    
    # Reason differs (metadata)
    assert "early_session" in m_early.reason, "Early session should be noted in reason"
    assert "normal_session" in m_late.reason, "Normal session should be noted in reason"
    
    print(f"  Early confidence: {m_early.confidence:.2f}, Late confidence: {m_late.confidence:.2f}")
    print("✓ Early session uses same path with different metadata")


def test_acceptance_hold_bars():
    """Verify hold bar accumulation grants permission."""
    
    engine = SessionMandateEngine()
    
    snap = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": 0.1,
        "seconds_since_open": 600,
    }
    
    # First tick: SUPPRESSED (no hold bars yet)
    m1 = engine.determine("SPY", snap)
    assert m1.state == RegimeState.SUPPRESSED, f"Should be SUPPRESSED initially: {m1.state}"
    assert m1.bias == "CALL"
    
    # Simulate time passing (hold bar accumulation happens in determine())
    # We need to manually advance the state for testing
    state = engine._get_acceptance_state("SPY")
    state["hold_bars"] = 2  # Manually set to threshold
    
    # Next tick: ENTRY_ALLOWED (hold bars met)
    m2 = engine.determine("SPY", snap)
    assert m2.state == RegimeState.ENTRY_ALLOWED, f"Should be ENTRY_ALLOWED after hold bars: {m2.state}"
    assert m2.allows_entry() == True
    
    print("✓ Hold bar accumulation grants permission")


def test_acceptance_range_break():
    """Verify range break grants permission."""
    
    engine = SessionMandateEngine()
    
    # Initial tick
    snap1 = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": 0.1,
        "seconds_since_open": 600,
    }
    
    m1 = engine.determine("SPY", snap1)
    assert m1.state == RegimeState.SUPPRESSED
    
    # Get state and set range_high manually
    state = engine._get_acceptance_state("SPY")
    state["range_high"] = 100.5
    
    # New tick with higher price (range break)
    snap2 = {
        "price": 100.6,  # Above range_high
        "vwap": 100.0,
        "vwap_dev": 0.6,
        "vwap_dev_change": 0.1,
        "seconds_since_open": 600,
    }
    
    m2 = engine.determine("SPY", snap2)
    assert m2.state == RegimeState.ENTRY_ALLOWED, f"Should be ENTRY_ALLOWED after range break: {m2.state}"
    assert m2.allows_entry() == True
    
    print("✓ Range break grants permission")


def test_cooldown_blocks_entry():
    """Verify post-exit cooldown blocks entry."""
    
    import time
    
    engine = SessionMandateEngine()
    
    snap = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": 0.1,
        "seconds_since_open": 600,
    }
    
    # Set exit timestamp (triggers cooldown)
    engine.set_last_exit_ts(time.monotonic())
    
    # Should be NO_TRADE during cooldown
    m = engine.determine("SPY", snap)
    assert m.state == RegimeState.NO_TRADE, f"Should be NO_TRADE during cooldown: {m.state}"
    assert m.allows_entry() == False
    assert "cooldown" in m.reason
    
    print("✓ Cooldown blocks entry")


def test_bias_flip_resets_acceptance():
    """Verify bias flip resets acceptance state."""
    
    engine = SessionMandateEngine()
    
    # CALL setup
    snap_call = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": 0.1,
        "seconds_since_open": 600,
    }
    
    m1 = engine.determine("SPY", snap_call)
    assert m1.bias == "CALL"
    
    # Manually add hold bars
    state = engine._get_acceptance_state("SPY")
    state["hold_bars"] = 2
    
    # Flip to PUT
    snap_put = {
        "price": 99.5,
        "vwap": 100.0,
        "vwap_dev": -0.5,
        "vwap_dev_change": -0.1,
        "seconds_since_open": 600,
    }
    
    m2 = engine.determine("SPY", snap_put)
    assert m2.bias == "PUT"
    
    # Hold bars should be reset
    state = engine._get_acceptance_state("SPY")
    assert state["hold_bars"] == 0, "Hold bars should reset on bias flip"
    
    # Should be SUPPRESSED again
    assert m2.state == RegimeState.SUPPRESSED
    
    print("✓ Bias flip resets acceptance")


def test_regime_classification_is_metadata():
    """Verify TREND vs RECLAIM doesn't affect permission."""
    
    engine = SessionMandateEngine()
    
    # TREND (location only, slope not aligned)
    snap_trend = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": -0.1,  # Negative slope (not aligned with CALL)
        "seconds_since_open": 600,
    }
    
    m_trend = engine.determine("SPY", snap_trend)
    assert m_trend.regime_type == "TREND", f"Should be TREND: {m_trend.regime_type}"
    
    engine._reset_acceptance_state("SPY")
    
    # RECLAIM (slope aligned)
    snap_reclaim = {
        "price": 100.5,
        "vwap": 100.0,
        "vwap_dev": 0.5,
        "vwap_dev_change": 0.1,  # Positive slope (aligned with CALL)
        "seconds_since_open": 600,
    }
    
    m_reclaim = engine.determine("SPY", snap_reclaim)
    assert m_reclaim.regime_type == "RECLAIM", f"Should be RECLAIM: {m_reclaim.regime_type}"
    
    # Both should have same state (SUPPRESSED, need hold bars)
    assert m_trend.state == m_reclaim.state, "Regime type should not affect state"
    
    print("✓ Regime classification is metadata only")


def test_reference_price_fallback():
    """Verify reference price fallback when VWAP unavailable."""
    
    engine = SessionMandateEngine()
    
    # Set opening price as fallback
    engine.set_reference_price("SPY", "open", 100.0)
    
    # Snap without VWAP
    snap_no_vwap = {
        "price": 100.5,
        "vwap": None,  # VWAP not available
        "vwap_dev": None,
        "vwap_dev_change": None,
        "seconds_since_open": 60,
    }
    
    m = engine.determine("SPY", snap_no_vwap)
    
    # Should NOT be NO_TRADE (VWAP absence is not a blocker)
    assert m.state != RegimeState.NO_TRADE or "no_reference" in m.reason, \
        f"VWAP absence alone should not cause NO_TRADE: {m.state}, {m.reason}"
    
    # Should use opening price as reference
    assert m.reference_price == 100.0, f"Should use open price as reference: {m.reference_price}"
    
    # Should detect CALL bias (price > open)
    assert m.bias == "CALL", f"Should detect CALL bias: {m.bias}"
    
    # Should be SUPPRESSED (needs acceptance)
    assert m.state == RegimeState.SUPPRESSED, f"Should be SUPPRESSED: {m.state}"
    
    # Reason should indicate VWAP unavailable
    assert "vwap_unavailable" in m.reason, f"Should note VWAP unavailable: {m.reason}"
    
    print("✓ Reference price fallback works when VWAP unavailable")


def test_onh_onl_fallback():
    """Verify ONH/ONL midpoint used when VWAP and open unavailable."""
    
    engine = SessionMandateEngine()
    
    # Set ONH/ONL as fallback
    engine.set_reference_price("SPY", "onh", 101.0)
    engine.set_reference_price("SPY", "onl", 99.0)
    # Midpoint = 100.0
    
    # Snap without VWAP
    snap_no_vwap = {
        "price": 100.5,
        "vwap": None,
        "vwap_dev": None,
        "vwap_dev_change": None,
        "seconds_since_open": 60,
    }
    
    m = engine.determine("SPY", snap_no_vwap)
    
    # Should use ONH/ONL midpoint as reference
    assert m.reference_price == 100.0, f"Should use ONH/ONL midpoint: {m.reference_price}"
    
    # Should detect CALL bias (price > midpoint)
    assert m.bias == "CALL", f"Should detect CALL bias: {m.bias}"
    
    print("✓ ONH/ONL fallback works")


def run_all_tests():
    print("\n" + "=" * 60)
    print(" SessionMandate Control Flow Tests ")
    print("=" * 60 + "\n")
    
    test_permission_is_binary()
    test_vwap_is_context_only()
    test_early_session_same_path()
    test_acceptance_hold_bars()
    test_acceptance_range_break()
    test_cooldown_blocks_entry()
    test_bias_flip_resets_acceptance()
    test_regime_classification_is_metadata()
    test_reference_price_fallback()
    test_onh_onl_fallback()
    
    print("\n" + "=" * 60)
    print(" ALL TESTS PASSED ")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    run_all_tests()