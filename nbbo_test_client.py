# nbbo_open_test.py
import os, asyncio, json, time
from datetime import datetime, timezone, timedelta
import websockets

API_KEY = os.getenv("MASSIVE_API_KEY")
WS_URL  = "wss://socket.massive.com/options"

# choose a small SPY/QQQ cluster around 500 (safe demo; we just need any live tick)
def today_yymmdd_et():
    # Use New York time for same-day exp
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return now_et.strftime("%y%m%d")

def topics_stock():
    return ["Q.SPY", "Q.QQQ"]

def topics_options():
    d = today_yymmdd_et()
    # 6 contracts SPY + 6 QQQ (C/P ±1 around 500)
    bases = [
        f"SPY{d}C00499000", f"SPY{d}C00500000", f"SPY{d}C00501000",
        f"SPY{d}P00499000", f"SPY{d}P00500000", f"SPY{d}P00501000",
        f"QQQ{d}C00499000", f"QQQ{d}C00500000", f"QQQ{d}C00501000",
        f"QQQ{d}P00499000", f"QQQ{d}P00500000", f"QQQ{d}P00501000",
    ]
    return [f"Q.O:{occ}" for occ in bases]

async def subscribe(ws, param):
    await ws.send(json.dumps({"action":"subscribe","params":param}))
    print(f"→ subscribed: {param}")

async def auth(ws):
    await ws.send(json.dumps({"action":"auth","params":API_KEY}))
    print("Auth sent.")

def is_open_et():
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return now >= open_t

async def main():
    if not API_KEY:
        print("ERROR: MASSIVE_API_KEY not set in env.")
        return

    print("Connecting to Massive NBBO WebSocket…")
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        await auth(ws)

        # Subscribe stock NBBO pre-open (sanity)
        for t in topics_stock():
            await subscribe(ws, t)

        # at 9:30:00 ET subscribe options
        # if already past open, do it immediately
        if not is_open_et():
            print("⏳ Waiting for 9:30:00 ET to subscribe options…")
            while not is_open_et():
                await asyncio.sleep(0.2)

        for t in topics_options():
            await subscribe(ws, t)

        print("\n📡 Waiting for NBBO ticks...")
        last_tick = time.time()
        warn_after = 20.0  # seconds without any tick → warn

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=warn_after)
                now = time.time()

                # Massive can batch messages in a single array
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if isinstance(data, dict):
                    data = [data]

                got_tick = False
                for msg in data:
                    ev = msg.get("ev")
                    if not ev:
                        continue

                    if ev == "status":
                        # connection/auth/subscribed messages
                        print(msg)
                        continue

                    if ev.startswith("Q"):
                        # NBBO (stock or option)
                        got_tick = True
                        sym = msg.get("sym") or msg.get("symbol")
                        bp  = msg.get("bp") or msg.get("bid")
                        ap  = msg.get("ap") or msg.get("ask")
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] {sym} bp={bp} ap={ap}")

                if got_tick:
                    last_tick = now

            except asyncio.TimeoutError:
                gap = time.time() - last_tick
                print(f"⚠️  No NBBO updates for {int(gap)}s… (still connected)")
                # keep listening; just warning
                last_tick = time.time()
            except websockets.exceptions.ConnectionClosedError as e:
                print(f"Disconnected: {e}. Re-run script.")
                break

if __name__ == "__main__":
    asyncio.run(main())
