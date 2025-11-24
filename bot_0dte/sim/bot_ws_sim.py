"""
WS-Native Simulator for 0DTE Trading Bot

Provides synthetic WebSocket event injection for testing strategies
without live market connections.

Architecture:
    SyntheticStocksWS → MassiveMux → Orchestrator → Strategy → ExecutionEngine (mock)
    SyntheticOptionsWS → MassiveMux → ChainAggregator → StrikeSelector

Features:
    • Synthetic underlying ticks with realistic price movement
    • Synthetic option chain (ATM ±2) with bid/ask spreads
    • VWAP tracking validation
    • Signal generation testing
    • Chain aggregation testing
    • Full pipeline execution without orders

Usage:
    python -m bot_0dte.sim.bot_ws_sim
"""

import asyncio
import time
from typing import Callable, List, Dict, Any
import random

from bot_0dte.orchestrator import Orchestrator
from bot_0dte.execution.engine import ExecutionEngine
from bot_0dte.infra.logger import StructuredLogger
from bot_0dte.infra.telemetry import Telemetry


# =====================================================================
# Synthetic WebSocket Adapters
# =====================================================================
class SyntheticStocksWS:
    """
    Synthetic stocks WebSocket adapter.

    Mimics MassiveStocksWSAdapter interface for testing.
    Allows manual tick injection.
    """

    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._underlying_handlers: List[Callable] = []
        self._connected = False

    def on_underlying(self, cb: Callable):
        """Register callback for underlying ticks."""
        self._underlying_handlers.append(cb)

    async def connect(self):
        """Simulate connection."""
        print("[SIM STOCKS WS] Connecting...")
        await asyncio.sleep(0.1)
        self._connected = True
        print("[SIM STOCKS WS] Connected ✅")

    async def subscribe(self, symbols: List[str]):
        """Simulate subscription."""
        num_symbols = len(symbols)
        print(f"[SIM STOCKS WS] Subscribed to {num_symbols} symbols: {symbols}")

    async def inject_tick(
        self, symbol: str, price: float, bid: float = None, ask: float = None
    ):
        """
        Inject synthetic underlying tick.

        Args:
            symbol: Trading symbol
            price: Current price
            bid: Bid price (defaults to price - 0.05)
            ask: Ask price (defaults to price + 0.05)
        """
        if bid is None:
            bid = price - 0.05
        if ask is None:
            ask = price + 0.05

        event = {
            "symbol": symbol,
            "price": price,
            "bid": bid,
            "ask": ask,
            "_recv_ts": time.time(),
        }

        # Dispatch to all registered callbacks
        for cb in self._underlying_handlers:
            await cb(event)

    async def close(self):
        """Simulate disconnection."""
        self._connected = False
        print("[SIM STOCKS WS] Disconnected")


class SyntheticOptionsWS:
    """
    Synthetic options WebSocket adapter.

    Mimics MassiveOptionsWSAdapter interface for testing.
    Allows manual NBBO tick injection.
    """

    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._nbbo_handlers: List[Callable] = []
        self._quote_handlers: List[Callable] = []
        self._connected = False
        self._subscriptions: List[str] = []

    def on_nbbo(self, cb: Callable):
        """Register callback for NBBO ticks."""
        self._nbbo_handlers.append(cb)

    def on_quote(self, cb: Callable):
        """Register callback for quote/greeks ticks."""
        self._quote_handlers.append(cb)

    async def connect(self):
        """Simulate connection."""
        print("[SIM OPTIONS WS] Connecting...")
        await asyncio.sleep(0.1)
        self._connected = True
        print("[SIM OPTIONS WS] Connected ✅")

    async def subscribe_contracts(self, occ_codes: List[str]):
        """Simulate contract subscription."""
        self._subscriptions.extend(occ_codes)
        num_contracts = len(occ_codes)
        print(f"[SIM OPTIONS WS] Subscribed to {num_contracts} contracts")

    async def inject_nbbo(
        self,
        symbol: str,
        contract: str,
        strike: float,
        right: str,
        bid: float,
        ask: float,
    ):
        """
        Inject synthetic NBBO tick.

        Args:
            symbol: Underlying symbol
            contract: OCC contract code
            strike: Strike price
            right: "C" or "P"
            bid: Bid price
            ask: Ask price
        """
        event = {
            "symbol": symbol,
            "contract": contract,
            "strike": strike,
            "right": right,
            "bid": bid,
            "ask": ask,
            "_recv_ts": time.time(),
        }

        # Dispatch to all registered callbacks
        for cb in self._nbbo_handlers:
            await cb(event)

    async def close(self):
        """Simulate disconnection."""
        self._connected = False
        print("[SIM OPTIONS WS] Disconnected")


# =====================================================================
# Synthetic MassiveMux
# =====================================================================
class SyntheticMux:
    """
    Synthetic mux that mimics MassiveMux interface.

    Routes synthetic events from test adapters to orchestrator.
    """

    def __init__(self, stocks_ws: SyntheticStocksWS, options_ws: SyntheticOptionsWS):
        self.stocks = stocks_ws
        self.options = options_ws
        self.loop = stocks_ws.loop

        self._underlying_handlers: List[Callable] = []
        self._option_handlers: List[Callable] = []

        self.contract_engine = None

    def on_underlying(self, cb: Callable):
        """Register underlying callback."""
        self._underlying_handlers.append(cb)

    def on_option(self, cb: Callable):
        """Register option callback."""
        self._option_handlers.append(cb)

    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        """Simulate connection and subscription."""
        num_symbols = len(symbols)
        print(f"[SIM MUX] Connecting with {num_symbols} symbols...")

        # Connect both adapters
        await self.stocks.connect()
        await self.options.connect()

        # Subscribe to underlyings
        await self.stocks.subscribe(symbols)

        # Wire callbacks
        self.stocks.on_underlying(self._handle_underlying)
        self.options.on_nbbo(self._handle_option)

        print("[SIM MUX] All connections established ✅")

    async def _handle_underlying(self, event: Dict[str, Any]):
        """Route underlying tick to orchestrator."""
        for cb in self._underlying_handlers:
            await cb(event)

    async def _handle_option(self, event: Dict[str, Any]):
        """Route option tick to orchestrator."""
        for cb in self._option_handlers:
            await cb(event)

    async def close(self):
        """Close all connections."""
        await self.stocks.close()
        await self.options.close()
        print("[SIM MUX] Closed")


# =====================================================================
# Price Generator (Realistic Movement)
# =====================================================================
class PriceGenerator:
    """
    Generate realistic price movements for simulation.

    Uses random walk with drift and mean reversion.
    """

    def __init__(self, initial_price: float, volatility: float = 0.002):
        self.price = initial_price
        self.volatility = volatility
        self.mean = initial_price

    def next_tick(self) -> float:
        """
        Generate next price tick.

        Returns:
            Next price (float)
        """
        # Random walk component
        change = random.gauss(0, self.volatility)

        # Mean reversion component (subtle)
        mean_reversion = (self.mean - self.price) * 0.01

        # Apply change
        self.price += change + mean_reversion

        return round(self.price, 2)


# =====================================================================
# Simulation Runner
# =====================================================================
class SimulationRunner:
    """
    Main simulation orchestrator.

    Manages synthetic adapters, price generation, and event injection.
    """

    def __init__(self, symbol: str = "SPY", initial_price: float = 450.0):
        self.symbol = symbol
        self.initial_price = initial_price

        # Components
        self.stocks_ws = SyntheticStocksWS()
        self.options_ws = SyntheticOptionsWS()
        self.mux = SyntheticMux(self.stocks_ws, self.options_ws)
        self.engine = None
        self.orch = None

        # Price generator
        self.price_gen = PriceGenerator(initial_price)

    async def setup(self):
        """Initialize all components."""
        print("\n" + "=" * 70)
        print(" WS-NATIVE SIMULATION SETUP ".center(70, "="))
        print("=" * 70 + "\n")

        # Create execution engine (mock mode)
        print("[SETUP] Creating execution engine (mock mode)...")
        self.engine = ExecutionEngine(use_mock=True)
        await self.engine.start()

        # Create orchestrator
        print("[SETUP] Creating orchestrator...")
        self.orch = Orchestrator(
            engine=self.engine,
            mux=self.mux,
            telemetry=Telemetry(),
            logger=StructuredLogger(),
            universe=[self.symbol],
            auto_trade_enabled=True,  # Enable trading in shadow mode
            trade_mode="shadow",  # Shadow = logs only, no real orders
        )

        # Start orchestrator (connects mux, registers callbacks)
        print("[SETUP] Starting orchestrator...")
        await self.orch.start()

        print("\n✅ Setup complete\n")

    def _generate_occ_code(
        self, strike: float, right: str, expiry: str = "251122"
    ) -> str:
        """
        Generate OCC contract code.

        Args:
            strike: Strike price
            right: "C" or "P"
            expiry: YYMMDD format

        Returns:
            OCC code (e.g., "O:SPY251122C00450000")
        """
        strike_int = int(round(strike * 1000))
        strike_str = f"{strike_int:08d}"
        right_upper = right.upper()
        return f"O:{self.symbol}{expiry}{right_upper}{strike_str}"

    async def inject_option_chain(self, underlying_price: float):
        """
        Inject synthetic option chain (ATM ±2).

        Args:
            underlying_price: Current underlying price
        """
        atm = round(underlying_price)
        strikes = [atm - 2, atm - 1, atm, atm + 1, atm + 2]

        for strike in strikes:
            # Calls
            call_mid = max(0.5, underlying_price - strike + random.uniform(-0.2, 0.2))
            call_bid = call_mid - 0.05
            call_ask = call_mid + 0.05

            await self.options_ws.inject_nbbo(
                symbol=self.symbol,
                contract=self._generate_occ_code(strike, "C"),
                strike=float(strike),
                right="C",
                bid=max(0.01, round(call_bid, 2)),
                ask=round(call_ask, 2),
            )

            # Puts
            put_mid = max(0.5, strike - underlying_price + random.uniform(-0.2, 0.2))
            put_bid = put_mid - 0.05
            put_ask = put_mid + 0.05

            await self.options_ws.inject_nbbo(
                symbol=self.symbol,
                contract=self._generate_occ_code(strike, "P"),
                strike=float(strike),
                right="P",
                bid=max(0.01, round(put_bid, 2)),
                ask=round(put_ask, 2),
            )

    async def run_simulation(self, num_ticks: int = 10):
        """
        Run simulation with synthetic ticks.

        Args:
            num_ticks: Number of underlying ticks to inject
        """
        print("\n" + "=" * 70)
        print(" SIMULATION RUNNING ".center(70, "="))
        print("=" * 70 + "\n")

        # Inject initial option chain
        print("[SIM] Injecting initial option chain (ATM ±2)...")
        await self.inject_option_chain(self.initial_price)
        print("[SIM] Injected 10 option contracts ✅\n")

        await asyncio.sleep(0.2)

        # Inject underlying ticks
        print(f"[SIM] Injecting {num_ticks} underlying ticks...\n")

        for i in range(num_ticks):
            # Generate next price
            price = self.price_gen.next_tick()

            tick_num = i + 1
            print(f"  Tick {tick_num}/{num_ticks}: {self.symbol} @ ${price:.2f}")

            # Inject underlying tick
            await self.stocks_ws.inject_tick(self.symbol, price)

            # Update option chain every 3 ticks
            if (i + 1) % 3 == 0:
                await self.inject_option_chain(price)

            # Small delay between ticks
            await asyncio.sleep(0.1)

        print("\n[SIM] All ticks injected ✅")

    async def show_results(self):
        """Display simulation results."""
        print("\n" + "=" * 70)
        print(" SIMULATION RESULTS ".center(70, "="))
        print("=" * 70 + "\n")

        # VWAP tracker state
        tracker = self.orch._vwap_tracker.get(self.symbol)
        if tracker:
            num_ticks = len(tracker.prices)
            vwap_val = tracker.last_vwap
            dev_val = tracker.last_dev
            print("📊 VWAP Tracker:")
            print(f"   Ticks tracked: {num_ticks}")
            print(f"   Last VWAP: ${vwap_val:.2f}")
            print(f"   Last deviation: ${dev_val:.2f}")
        else:
            print("⚠️  VWAP tracker not found")

        # Chain aggregator state
        chain = self.orch.chain_agg.get_chain(self.symbol)
        num_options = len(chain)
        is_fresh = self.orch.chain_agg.is_fresh(self.symbol)
        print(f"\n📈 Chain Aggregator:")
        print(f"   Options cached: {num_options}")
        print(f"   Chain fresh: {is_fresh}")

        if chain:
            calls = [o for o in chain if o["right"] == "C"]
            puts = [o for o in chain if o["right"] == "P"]
            num_calls = len(calls)
            num_puts = len(puts)
            print(f"   Calls: {num_calls}, Puts: {num_puts}")

            # Show sample option
            if calls:
                sample = calls[0]
                strike_val = sample["strike"]
                premium_val = sample["premium"]
                bid_val = sample["bid"]
                ask_val = sample["ask"]
                print("\n   Sample Call:")
                print(f"   Strike: {strike_val}")
                print(f"   Premium: ${premium_val:.2f}")
                print(f"   Bid/Ask: ${bid_val:.2f} / ${ask_val:.2f}")

        # Strategy stats (if telemetry available)
        print("\n🎯 Strategy:")
        print("   Signals generated: (check logs above)")
        print("   Trades executed: (shadow mode - logged only)")

        print("\n" + "=" * 70 + "\n")

    async def cleanup(self):
        """Clean up resources."""
        print("[CLEANUP] Shutting down...")
        await self.mux.close()
        print("[CLEANUP] Complete ✅")


# =====================================================================
# Main Entry Point
# =====================================================================
async def main():
    """
    Run WS-native simulation.

    Simulates:
        1. WebSocket connections
        2. Underlying tick stream
        3. Option chain NBBO stream
        4. VWAP calculation
        5. Signal generation
        6. Strike selection
        7. Trade execution (shadow mode)
    """

    # Configuration
    SYMBOL = "SPY"
    INITIAL_PRICE = 450.0
    NUM_TICKS = 10

    # Create and run simulation
    sim = SimulationRunner(symbol=SYMBOL, initial_price=INITIAL_PRICE)

    try:
        # Setup
        await sim.setup()

        # Run simulation
        await sim.run_simulation(num_ticks=NUM_TICKS)

        # Show results
        await sim.show_results()

        # Success
        print("✅ Simulation completed successfully!\n")

    except Exception as e:
        print(f"\n❌ Simulation failed: {e}\n")
        import traceback

        traceback.print_exc()

    finally:
        # Cleanup
        await sim.cleanup()


if __name__ == "__main__":
    """
    Run simulation from command line.

    Usage:
        python -m bot_0dte.sim.bot_ws_sim

    Or:
        cd bot_0dte/sim
        python bot_ws_sim.py
    """
    asyncio.run(main())
