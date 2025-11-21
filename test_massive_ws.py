import asyncio
from bot_0dte.data.adapters.massive_ws_adapter import MassiveWSAdapter

async def main():
    # Load Massive API key from environment
    ws = MassiveWSAdapter.from_env()

    # Simple print handlers for debugging
    async def on_u(event):
        print("UNDERLYING:", event)

    async def on_o(event):
        print("OPTION:", event)

    ws.on_underlying(on_u)
    ws.on_option(on_o)

    print("Connecting Massive WS…")
    await ws.connect()
    print("Connected!")

    # Subscribe to your universe
    underlyings = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMZN", "MSFT", "META"]
    await ws.subscribe_underlyings(underlyings)

    # Subscribe to option chains for SPY + QQQ (adapter handles mapping)
    await ws.subscribe_options(["SPY", "QQQ"])

    print("Listening for 5 seconds…\n")
    await asyncio.sleep(5)

    await ws.close()
    print("WS closed.")

asyncio.run(main())

