import sys
import os
import asyncio

# Ensure project root is on the Python path
sys.path.insert(0, os.path.abspath("."))

# IMPORTANT: Set up a fresh event loop *before* any import that might use asyncio
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Now import your bot modules
from bot_0dte.orchestrator import Orchestrator
from bot_0dte.execution.engine import ExecutionEngine
from shared.bus.mux import Mux
from shared.telemetry.telemetry import Telemetry
from shared.config import load_config
from shared.logger import StructuredLogger

# Initialize dependencies
config = load_config()
telemetry = Telemetry(config)
logger = StructuredLogger("force-entry-test", config)
mux = Mux()
engine = ExecutionEngine()  # ExecutionEngine does not take config/telemetry in your code

# Instantiate orchestrator
orch = Orchestrator(
    engine=engine,
    mux=mux,
    telemetry=telemetry,
    logger=logger,
    config=config
)

# Async entry test
async def run_force():
    print("[TEST] Starting orchestrator...")
    await orch.force_entry("SPY", "CALL")
    print("[TEST] Done forced entry.")

# Run it
loop.run_until_complete(run_force())
