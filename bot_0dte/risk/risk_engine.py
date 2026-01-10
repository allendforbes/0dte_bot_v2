from .exposure_premium import size_from_premium

class RiskEngine:
    def __init__(self, account_state, config, decision_logger):
        self.account_state = account_state
        self.cfg = config
        self.log = decision_logger

    async def approve(self, trade_intent):
        equity = self.account_state.get_equity()
        print(f"[RISK] equity={equity}")

        option_price = trade_intent["option_price"]

        qty = size_from_premium(
            equity=equity,
            option_price=option_price,
            exposure_pct=self.cfg.EXPOSURE_PCT,
            stop_pct=self.cfg.STOP_PCT,
        )

        symbol = trade_intent["symbol"]

        if qty == 0:
            self.log.log(
            decision="RISK_REJECT",
            symbol=symbol,
            reason="insufficient_risk_budget",
            convexity_score=1.0,
            tier="RISK",
            price=option_price,
        )
            return None

        self.log.log(
            decision="RISK_APPROVE",
            symbol=symbol,
            reason="approved",
            convexity_score=1.0,
            tier="RISK",
            price=option_price,
        )


