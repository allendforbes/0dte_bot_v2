import asyncio
import os
from bot_0dte.data.providers.massive.massive_options_ws_adapter import MassiveOptionsWSAdapter

# Replace with real OCCs for today (or let Mux/engine drive it in bot)
OCCS = [
    # "SPY20250221C00400000", "SPY20250221P00400000",
]

async def main():
    os.environ.setdefault("MASSIVE_API_KEY", "<YOUR_KEY>")
    ad = MassiveOptionsWSAdapter()

    async def on_opt(ev):
        print("NBBO:", ev["contract"], ev["bid"], ev["ask"])

    ad.on_option(on_opt)
    ad.set_occ_subscriptions(OCCS)
    await ad.connect()
    await asyncio.sleep(5)  # see some ticks
    await ad.close()

if __name__ == "__main__":
    asyncio.run(main())
