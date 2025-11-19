import asyncio
from typing import List, Dict, Any, Optional
from ib_insync import Option, ContractDetails

from bot_0dte.infra.telemetry import TelemetryEvent


class IBKRChainBridge:
    """
    IBKRChainBridge — async-safe, bounded-latency option chain fetcher.

    Responsibilities:
        • reqContractDetails() → normalized chain entries
        • reqMktData() for each contract (snapshot only)
        • bounded executor calls (max timeout)
        • normalized schema for StrikeSelector
        • no handshake logic (adapter/SessionController owns IB instance)

    Guarantees:
        • No blocking calls in event loop
        • No unbounded sleeps
        • IB pacing respected
        • Missing greeks handled safely
    """

    def __init__(self, ib, journaling_cb=None, timeout: float = 3.0):
        self.ib = ib
        self.timeout = timeout
        self.journaling_cb = journaling_cb

    # -----------------------------------------------------------
    # Internal helper: async-safe wrapper for qualifyContracts
    # -----------------------------------------------------------
    async def _qualify(self, contract):
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, self.ib.qualifyContracts, contract)

        try:
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("qualifyContracts timeout")

    # -----------------------------------------------------------
    # PUBLIC: FETCH OPTION CHAIN
    # -----------------------------------------------------------
    async def fetch_chain(
        self,
        symbol: str,
        expiry: str,
        strikes: Optional[List[float]] = None,
        right: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch normalized option chain for:
            symbol, expiry (YYYY-MM-DD), optional strikes & side filter.

        Schema returned:
            [
              {
                "symbol": str,
                "expiry": str,
                "strike": float,
                "right": 'C' or 'P',
                "conId": int,
                "contract": ib_insync.Option,
                "bid": float,
                "ask": float,
                "last": float,
                "iv": float or None,
                "delta": float or None,
                "gamma": float or None,
                "theta": float or None,
                "vega": float or None,
              },
              ...
            ]
        """

        # -------------------------------------------------------
        # 1. Build template for chain lookup
        # -------------------------------------------------------
        contract_month = expiry.replace("-", "")  # '20240314'
        template = Option(
            symbol=symbol,
            lastTradeDateOrContractMonth=contract_month,
            strike=0,
            right="C",
            exchange="SMART",
            currency="USD",
        )

        # -------------------------------------------------------
        # 2. Get full contract details from IBKR
        # -------------------------------------------------------
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, self.ib.reqContractDetails, template)

        try:
            details: List[ContractDetails] = await asyncio.wait_for(
                fut, timeout=self.timeout
            )
        except asyncio.TimeoutError:
            raise RuntimeError("reqContractDetails timeout")

        if not details:
            return []

        # -------------------------------------------------------
        # 3. Normalize chain (filter strikes/right if needed)
        # -------------------------------------------------------
        chain = []
        for d in details:
            c = d.contract

            if strikes and c.strike not in strikes:
                continue
            if right and c.right.upper() != right.upper():
                continue

            chain.append(
                {
                    "symbol": c.symbol,
                    "expiry": expiry,
                    "strike": float(c.strike),
                    "right": c.right.upper(),
                    "conId": int(c.conId),
                    "contract": c,
                }
            )

        if not chain:
            return []

        # -------------------------------------------------------
        # 4. Market data snapshots (bid/ask/last/greeks)
        # -------------------------------------------------------
        def _snapshot_contracts(rows: List[Dict[str, Any]]):
            out = []

            for row in rows:
                c = row["contract"]

                ticker = self.ib.reqMktData(c, "", False, False)

                # micro-pacing: ~50ms is stable for chains < 60 rows
                self.ib.sleep(0.05)

                greeks = ticker.modelGreeks

                out.append(
                    {
                        **row,
                        "bid": ticker.bid,
                        "ask": ticker.ask,
                        "last": ticker.last,
                        "iv": greeks.impliedVol if greeks else None,
                        "delta": greeks.delta if greeks else None,
                        "gamma": greeks.gamma if greeks else None,
                        "theta": greeks.theta if greeks else None,
                        "vega": greeks.vega if greeks else None,
                    }
                )

            return out

        try:
            enriched = await asyncio.wait_for(
                loop.run_in_executor(None, _snapshot_contracts, chain),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("market data snapshot timeout")

        # -------------------------------------------------------
        # 5. Journaling hook (telemetry)
        # -------------------------------------------------------
        if self.journaling_cb:
            await self.journaling_cb(
                TelemetryEvent(
                    event="chain_fetch",
                    payload={
                        "symbol": symbol,
                        "expiry": expiry,
                        "count": len(enriched),
                    },
                )
            )

        return enriched

    # -----------------------------------------------------------
    # PUBLIC: FETCH SINGLE CONTRACT SNAPSHOT
    # -----------------------------------------------------------
    async def fetch_contract_snapshot(self, contract) -> Dict[str, Any]:
        """
        Fetch real-time greeks + bid/ask/last for one contract.
        """

        def _snap():
            ticker = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(0.05)
            g = ticker.modelGreeks
            return {
                "bid": ticker.bid,
                "ask": ticker.ask,
                "last": ticker.last,
                "iv": g.impliedVol if g else None,
                "delta": g.delta if g else None,
                "gamma": g.gamma if g else None,
                "theta": g.theta if g else None,
                "vega": g.vega if g else None,
            }

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _snap),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("contract snapshot timeout")
