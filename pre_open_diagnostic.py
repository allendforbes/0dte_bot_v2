import asyncio
import json
import websockets
import time
import datetime as dt

API_KEY = "8JaZ0si58BE3KjHD8w20_n6evvIpVm7b"
WS_URL = "wss://socket.massive.com/options"

# Choose SPY & QQQ ATM call contracts based on today's date
def occ_for_today(symbol, strike=500):
    today = dt.date.today().strftime("%y%m%d")
    return f"Q.O:{symbol}{today}C{strike:08d}"

SPY_CONTRACT = occ_for_today("SPY", 500)
QQQ_CONTRACT = occ_for_today("QQQ", 400)

async def run_diagnostic():
    print("\n===== MASSIVE PRE-OPEN DIAGNOSTIC =====\n")
    print("Connecting to Massive NBBO WebSocket…\n")

    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        # AUTH
        await ws.send(json.dumps({"action": "auth", "params": API_KEY}))
        auth_resp = json.loads(await ws.recv())
        print("Auth Response:", auth_resp)

        # SUBSCRIBE to SPY & QQQ
        to_sub = [SPY_CONTRACT, QQQ_CONTRACT]
        print("\nSubscribing to:", to_sub)

        await ws.send(json.dumps({"action": "subscribe", "params": to_sub}))

        # Listen for responses
        sub_resp = json.loads(await ws.recv())
        print("Subscription Response:", sub_resp)

        print("\n📡 Waiting for NBBO ticks (press Ctrl+C to exit)…\n")
        print("If market is closed, you may receive zero ticks. This is normal.\n")

        # Tick loop
        while True:
            raw = await ws.recv()
            ts = time.time()

            try:
                msg = json.loads(raw)
            except:
                print("JSON error:", raw)
                continue

            if isinstance(msg, list):
                for m in msg:
                    await handle_msg(m, ts)
            else:
                await handle_msg(msg, ts)

async def handle_msg(msg, recv_ts):
    ev = msg.get("ev")
    if ev == "status":
        print("[status]", msg)
        return

    if ev and ev.startswith("Q"):
        contract = msg.get("sym") or msg.get("contract")
        bid = msg.get("bp") or msg.get("bid")
        ask = msg.get("ap") or msg.get("ask")
        bt = msg.get("bt") or msg.get("t")

        latency_ms = (recv_ts - (bt / 1000 if isinstance(bt, int) else recv_ts)) * 1000

        print(
            f"[NBBO] {contract}  bid={bid} ask={ask}  latency={latency_ms:.1f}ms"
        )

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
