# ------------------------------------------------------------
# Option Trend Validator (DIAGNOSTIC ONLY)
# ------------------------------------------------------------
# Purpose:
#   Observe whether the selected option contract is
#   structurally trending at entry time.
#
# Guarantees:
#   - No state mutation
#   - No execution gating (yet)
#   - Purely observational
# ------------------------------------------------------------

from typing import Dict, Any


class OptionTrendValidator:
    def __init__(self, chain_aggregator):
        self.chain_agg = chain_aggregator

    async def observe(
        self,
        symbol: str,
        contract: str | None,
        bias: str,
        ts: float,
    ) -> Dict[str, Any]:
        """
        Observe option trend characteristics for diagnostics.

        Returns a dict suitable for structured logging.
        """

        result = {
            "symbol": symbol,
            "contract": contract,
            "bias": bias,
            "timestamp": ts,
            "option_found": False,
            "bid": None,
            "ask": None,
            "mid": None,
        }

        if not contract:
            result["reason"] = "no_contract_provided"
            return result

        chain = self.chain_agg.get_chain(symbol)
        if not chain:
            result["reason"] = "empty_chain"
            return result

        row = next((r for r in chain if r.get("contract") == contract), None)
        if not row:
            result["reason"] = "contract_not_in_chain"
            return result

        bid = row.get("bid")
        ask = row.get("ask")

        if bid is None or ask is None:
            result["reason"] = "missing_nbbo"
            return result

        mid = (bid + ask) / 2

        result.update({
            "option_found": True,
            "bid": bid,
            "ask": ask,
            "mid": mid,
        })

        # NOTE:
        # VWAP / delta / range expansion intentionally NOT enforced yet.
        # This is phase-1 observability only.

        return result
