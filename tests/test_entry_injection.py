import pytest
from unittest.mock import AsyncMock, Mock

from bot_0dte.orchestrator import Orchestrator
from bot_0dte.infra.phase import ExecutionPhase

# ----------------------------------------------------------------
# Test Constants
# ----------------------------------------------------------------
MAX_QTY = 1
MAX_DOLLAR_RISK = 100


# ----------------------------------------------------------------
# Mock Dependencies
# ----------------------------------------------------------------
class MockEngine:
    def __init__(self):
        self.account_state = Mock()

    async def send_bracket(self, *args, **kwargs):
        # Simulate a successful mock fill
        return {"status": "mock-filled"}


class MockMux:
    def on_underlying(self, fn):
        pass

    def on_option(self, fn):
        pass

    async def connect(self, symbols, expiry_map):
        # Do nothing
        return


class MockTelemetry:
    pass


class MockLogger:
    def log_event(self, *args, **kwargs):
        pass


# ----------------------------------------------------------------
# Test
# ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_attempt_entry_calls_execute():
    """
    If:
      - SessionMandate allows entry
      - Strike selection returns success
      - Risk approves the trade
    Then:
      - Orchestrator._attempt_entry should call _execute_entry exactly once
    """

    orch = Orchestrator(
        engine=MockEngine(),
        mux=MockMux(),
        telemetry=MockTelemetry(),
        logger=MockLogger(),
        config={"MAX_QTY": MAX_QTY, "MAX_DOLLAR_RISK": MAX_DOLLAR_RISK},
        auto_trade_enabled=True,
        execution_phase=ExecutionPhase.SHADOW,
    )

    # Patch the execution method
    orch._execute_entry = AsyncMock()

    # Mock strike selector so select() returns a success
    strike = Mock(
        success=True,
        contract="SPY250108C00690000",
        strike=690,
        premium=0.90,
        bid=0.85,
        ask=0.95,
        right="C",
        expiry="20260108",
    )

    strike.as_legacy_dict.return_value = {
        "contract": strike.contract,
        "strike": strike.strike,
        "premium": strike.premium,
        "bid": strike.bid,
        "ask": strike.ask,
        "right": strike.right,
        "expiry": strike.expiry,
    }

    orch.selector = Mock()
    orch.selector.select.return_value = strike

    # Mock risk_engine to approve with 1 contract
    orch.risk_engine = Mock()
    orch.risk_engine.approve = AsyncMock(return_value=Mock(contracts=1))

    # Create a fake mandate that *allows entry*
    mandate = Mock(
        bias="CALL",
        grade="L0",
        score=0.9,
        regime="TREND",
        regime_type="TREND",   # ✅ REQUIRED
        confidence=0.9,        # ✅ REQUIRED (must be float)
    )

    # Chain rows (can be empty since select() is already mocked)
    chain_rows = [{}]

    # Call the helper under test
    result = await orch._attempt_entry("SPY", mandate, 689.50, chain_rows)

    # Assert that the helper returned True (entry was attempted)
    assert result is True

    # Assert _execute_entry was actually called exactly once
    orch._execute_entry.assert_awaited_once()
