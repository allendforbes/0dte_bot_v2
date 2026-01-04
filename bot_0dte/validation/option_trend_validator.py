# ------------------------------------------------------------
# Option Trend Validator (DIAGNOSTIC ONLY)
# ------------------------------------------------------------
# Purpose:
#   Observe whether the selected option contract is
#   structurally trending at entry time.
#
# Guarantees:
#   - No state mutation
#   - No execution gating
#   - Never raises
#   - Purely observational / logging only
# ------------------------------------------------------------

from typing import Dict, Any, Optional


class OptionTrendValidator:
    def __init__(self, chain_aggregator):
        self.chain_agg = chain_aggregator

    async def observe(
        self,
        *,
        symbol: str,
        bias: Optional[str] = None,
        contract: Optional[str] = None,
        chain: Optional[list] = None,
        ts: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Observe option trend characteristics for diagnostics only.

        This method MUST:
        - Never raise
        - Never block entry
        - Accept extra arguments safely
        """

        result: Dict[str, Any] = {
            "symbol": symbol,
            "bias": bias,
            "contract": contract,
            "timestamp": ts,
            "option_found": False,
            "bid": None,
            "ask": None,
            "mid": None,
            "status": "ok",
        }

        try:
            # --------------------------------------------------
            # Resolve chain source
            # --------------------------------------------------
            if chain is None:
                chain = self.chain_agg.get_chain(symbol)

            if not chain:
                result["status"] = "skipped"
                result["reason"] = "empty_chain"
                return result

            if not contract:
                result["status"] = "skipped"
                result["reason"] = "no_contract_provided"
                return result

            # --------------------------------------------------
            # Locate contract row
            # --------------------------------------------------
            row = next((r for r in chain if r.get("contract") == contract), None)
            if not row:
                result["status"] = "skipped"
                result["reason"] = "contract_not_in_chain"
                return result

            bid = row.get("bid")
            ask = row.get("ask")

            if bid is None or ask is None or bid <= 0 or ask <= 0:
                result["status"] = "skipped"
                result["reason"] = "invalid_nbbo"
                return result

            mid = (bid + ask) / 2

            result.update({
                "option_found": True,
                "bid": bid,
                "ask": ask,
                "mid": mid,
            })

            # --------------------------------------------------
            # NOTE:
            #   No enforcement here (yet):
            #   - VWAP
            #   - delta
            #   - gamma
            #   - range expansion
            # --------------------------------------------------

            return result

        except Exception as e:
            # ABSOLUTE GUARANTEE: diagnostics never block trading
            result["status"] = "error"
            result["error"] = str(e)
            return result
