import asyncio
import json
import logging
from time import monotonic

import websockets

logger = logging.getLogger(__name__)


class MarketDataWSAdapter:
    """
    Real-time MarketData.app websocket adapter.

    Responsibilities:
        - Maintain websocket connection
        - Parse JSON payloads
        - Push (ts_recv, payload) into a bounded asyncio.Queue
        - Reconnect on failure
        - Heartbeat monitoring
        - No direct link to strategy / orchestrator
    """

    def __init__(self, api_key: str, symbols: list[str], queue: asyncio.Queue):
        self.api_key = api_key
        self.symbols = symbols
        self.queue = queue

        # Correct WebSocket URL for MarketData.app (Trader Plan)
        symbols_param = ",".join(self.symbols)
        self.ws_url = (
            f"wss://api.marketdata.app/v1/stream/stocks"
            f"?symbols={symbols_param}"
            f"&token={self.api_key}"
        )

        self.running = True
        self.last_heartbeat = monotonic()
        self.heartbeat_timeout = 20.0

    async def run(self) -> None:
        """
        Persistent websocket loop with automatic reconnect.
        Writes raw messages into self.queue as (ts_recv, payload).
        """
        while self.running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    max_size=1_000_000,
                    ping_interval=10,
                    ping_timeout=5,
                ) as ws:
                    logger.info("md.ws_connect")

                    # If the WS requires a subscription message, send it here.
                    # Adjust payload to match MarketData.app WS docs.
                    await ws.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "symbols": self.symbols,
                            }
                        )
                    )

                    async for msg in ws:
                        ts_recv = monotonic()
                        self.last_heartbeat = ts_recv

                        try:
                            payload = json.loads(msg)
                        except Exception:
                            logger.warning("md.ws_bad_json")
                            continue

                        # Bounded queue: drop oldest tick if full
                        if self.queue.full():
                            _ = self.queue.get_nowait()
                            logger.warning("md.ws_queue_overflow_drop")

                        await self.queue.put((ts_recv, payload))

            except Exception as e:
                logger.error(f"md.ws_error: {e}")
                await asyncio.sleep(1.0)
                logger.info("md.ws_reconnect")

    def stop(self) -> None:
        """Signal the adapter to stop. Tasks are cancelled by the feed."""
        self.running = False
