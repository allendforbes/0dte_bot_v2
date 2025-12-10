import os
import json
import asyncio
import websockets

URL = "wss://socket.massive.com/options"
API = os.getenv("MASSIVE_API_KEY")

async def sniff():
    print("Using API key:", API[:6] + "..." if API else "MISSING!")
    async with websockets.connect(URL, ping_interval=None) as ws:
        print("Connected ✓")

        await ws.send(json.dumps({"action": "auth", "params": API}))
        print("Auth sent")

        topic = "Q.O:SPY251210C00500000"   # one of your OCC codes
        await ws.send(json.dumps({"action": "subscribe", "params": [topic]}))
        print("Subscribed:", topic)

        print("Listening… (CTRL+C to stop)")
        while True:
            raw = await ws.recv()
            print("\nRAW:", raw)

if __name__ == "__main__":
    asyncio.run(sniff())
