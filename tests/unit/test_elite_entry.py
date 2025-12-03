import pytest
from bot_0dte.strategy.elite_entry import EliteEntryEngine

class TestTrendEarly:
    def test_call_trend(self):
        engine = EliteEntryEngine()
        snap = {
            "symbol": "SPY",
            "price": 500.0,
            "vwap": 501.0,
            "vwap_dev": -1.0,
            "vwap_dev_change": 0.03,
            "seconds_since_open": 1200,
        }
        sig = engine.qualify(snap)
        assert sig is not None
        assert sig.bias == "CALL"
        assert sig.regime == "TREND_EARLY"

    def test_put_trend(self):
        engine = EliteEntryEngine()
        snap = {
            "symbol": "SPY",
            "price": 502,
            "vwap": 501,
            "vwap_dev": 1,
            "vwap_dev_change": -0.03,
            "seconds_since_open": 800,
        }
        sig = engine.qualify(snap)
        assert sig is not None
        assert sig.bias == "PUT"


class TestHardVetoes:
    def test_time_window(self):
        e = EliteEntryEngine()
        snap = {
            "symbol":"SPY",
            "price":500,
            "vwap":501,
            "vwap_dev":-1,
            "vwap_dev_change":0.03,
            "seconds_since_open":5500
        }
        assert e.qualify(snap) is None

    def test_min_slope(self):
        e = EliteEntryEngine()
        snap = {
            "symbol":"SPY","price":500,"vwap":501,
            "vwap_dev":-1,"vwap_dev_change":0.001,
            "seconds_since_open":1000
        }
        assert e.qualify(snap) is None

    def test_min_dev(self):
        e = EliteEntryEngine()
        snap = {
            "symbol":"SPY","price":500,"vwap":501,
            "vwap_dev":0.01,"vwap_dev_change":0.03,
            "seconds_since_open":1000
        }
        assert e.qualify(snap) is None


class TestBoosts:
    def test_micro_boost(self):
        e = EliteEntryEngine()
        snap = {
            "symbol":"SPY","price":500,"vwap":501,
            "vwap_dev":-1,"vwap_dev_change":0.03,
            "seconds_since_open":1800,
            "upvol_pct":70,"flow_ratio":1.3,
            "iv_change":0.05,"skew_shift":0.02,
        }
        sig = e.qualify(snap)
        assert sig.score >= 80

    def test_strong_slope_boost(self):
        e = EliteEntryEngine()
        snap = {
            "symbol":"SPY","price":500,"vwap":501,
            "vwap_dev":-1,"vwap_dev_change":0.06,
            "seconds_since_open":1800,
        }
        sig = e.qualify(snap)
        assert sig.score == 80  # 70 + 10 strong slope


class TestGrading:
    def test_a_plus(self):
        e = EliteEntryEngine()
        snap = {
            "symbol":"SPY",
            "price":500,"vwap":501,"vwap_dev":-1,
            "vwap_dev_change":0.06,
            "seconds_since_open":1200,
            "upvol_pct":70,"flow_ratio":1.3,
            "iv_change":0.05,"skew_shift":0.02,
        }
        sig = e.qualify(snap)
        assert sig.grade == "A+"
        assert sig.trail_mult == 1.40
