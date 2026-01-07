from .exposure_premium import size_from_premium

class RiskEngine:
    def __init__(self, account_state, config, decision_logger):
        self.account_state = account_state
        self.cfg = config
        self.log = decision_logger

    async def approve(self, trade_intent):
        equity = await self.account_state.get_equity()

        qty = size_from_premium(
            equity=equity,
            option_price=trade_intent.option_price,
            exposure_pct=self.cfg.EXPOSURE_PCT,
            stop_pct=self.cfg.STOP_PCT,
        )

        if qty == 0:
            self.log.log(
                decision="RISK_REJECT",
                reason="insufficient_risk_budget",
                equity=equity,
                option_price=trade_intent.option_price,
            )
            return None

        self.log.log(
            decision="RISK_APPROVE",
            equity=equity,
            option_price=trade_intent.option_price,
            contracts=qty,
            exposure_pct=self.cfg.EXPOSURE_PCT,
            stop_pct=self.cfg.STOP_PCT,
            max_loss=qty * trade_intent.option_price * 100 * self.cfg.STOP_PCT,
        )

        return trade_intent.with_contracts(qty)
