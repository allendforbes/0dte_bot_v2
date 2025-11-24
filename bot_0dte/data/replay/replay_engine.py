"""
ReplayEngine — WS-Native Historical Data Playback

Pure event-driven replay system that integrates with MassiveMux architecture.
No REST, no MarketDataFeed, no chain_bridge.

Features:
    • Load historical recordings (JSONL or JSON)
    • Inject events through MassiveMux (same as live WS)
    • Time scaling (real-time, fast-forward, instant)
    • Full orchestrator integration
    • VWAP + ChainAggregator compatibility

Event Format (JSONL):
    {"type": "underlying", "symbol": "SPY", "price": 450.23, "bid": 450.20,
     "ask": 450.25, "_recv_ts": 1700000000.123}

    {"type": "nbbo", "symbol": "SPY", "contract": "O:SPY241122C00450000",
     "strike": 450.0, "right": "C", "bid": 0.95, "ask": 1.05,
     "_recv_ts": 1700000000.125}

Usage:
    replay = ReplayEngine("./data/replays/spy_20241122.jsonl", mux, orch)
    await replay.play(speed=2.0)  # 2x speed
    await replay.play(speed=0.0)  # instant (no delays)
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# =====================================================================
# Replay Event
# =====================================================================
@dataclass
class ReplayEvent:
    """
    Normalized replay event.

    Attributes:
        type: "underlying" or "nbbo"
        timestamp: Unix timestamp (seconds)
        data: Event payload dict
    """

    type: str
    timestamp: float
    data: Dict[str, Any]


# =====================================================================
# ReplayEngine
# =====================================================================
class ReplayEngine:
    """
    WS-native replay engine.

    Loads historical events and injects them through MassiveMux
    exactly as live WebSocket events would arrive.
    """

    def __init__(self, filepath: str, mux, orchestrator):
        """
        Initialize replay engine.

        Args:
            filepath: Path to replay file (JSONL or JSON)
            mux: MassiveMux or SyntheticMux instance
            orchestrator: Orchestrator instance
        """
        self.filepath = Path(filepath)
        self.mux = mux
        self.orchestrator = orchestrator

        self.events: List[ReplayEvent] = []
        self.loaded = False

        # Stats
        self.total_events = 0
        self.underlying_events = 0
        self.option_events = 0
        self.duration_seconds = 0.0

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load(self):
        """
        Load replay file into memory.

        Supports:
            • JSONL (JSON lines, one event per line)
            • JSON (array of events)

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format invalid
        """
        if not self.filepath.exists():
            raise FileNotFoundError(f"Replay file not found: {self.filepath}")

        logger.info(f"[REPLAY] Loading {self.filepath}...")

        # Determine format
        if self.filepath.suffix == ".jsonl":
            self._load_jsonl()
        elif self.filepath.suffix == ".json":
            self._load_json()
        else:
            raise ValueError(f"Unsupported file format: {self.filepath.suffix}")

        # Sort by timestamp
        self.events.sort(key=lambda e: e.timestamp)

        # Calculate stats
        self.total_events = len(self.events)
        self.underlying_events = sum(1 for e in self.events if e.type == "underlying")
        self.option_events = sum(1 for e in self.events if e.type == "nbbo")

        if self.events:
            first_ts = self.events[0].timestamp
            last_ts = self.events[-1].timestamp
            self.duration_seconds = last_ts - first_ts

        self.loaded = True

        logger.info(f"[REPLAY] Loaded {self.total_events} events:")
        logger.info(f"         Underlying: {self.underlying_events}")
        logger.info(f"         Options: {self.option_events}")
        logger.info(f"         Duration: {self.duration_seconds:.1f}s")

    def _load_jsonl(self):
        """Load JSONL format (one JSON object per line)."""
        with open(self.filepath, "r") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    event_dict = json.loads(line)
                    event = self._parse_event(event_dict, line_num)
                    if event:
                        self.events.append(event)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[REPLAY] Skipping invalid JSON on line {line_num}: {e}"
                    )
                except Exception as e:
                    logger.warning(f"[REPLAY] Skipping event on line {line_num}: {e}")

    def _load_json(self):
        """Load JSON format (array of events)."""
        with open(self.filepath, "r") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("JSON file must contain an array of events")

        for idx, event_dict in enumerate(data, 1):
            try:
                event = self._parse_event(event_dict, idx)
                if event:
                    self.events.append(event)
            except Exception as e:
                logger.warning(f"[REPLAY] Skipping event {idx}: {e}")

    def _parse_event(
        self, event_dict: Dict[str, Any], line_num: int
    ) -> Optional[ReplayEvent]:
        """
        Parse raw event dict into ReplayEvent.

        Args:
            event_dict: Raw event dict from file
            line_num: Line/index number for error reporting

        Returns:
            ReplayEvent or None if invalid
        """
        event_type = event_dict.get("type")
        timestamp = event_dict.get("_recv_ts") or event_dict.get("timestamp")

        if not event_type or not timestamp:
            logger.warning(f"[REPLAY] Event {line_num} missing type or timestamp")
            return None

        if event_type not in ("underlying", "nbbo"):
            logger.warning(f"[REPLAY] Event {line_num} has invalid type: {event_type}")
            return None

        # Validate required fields
        if event_type == "underlying":
            if not all(k in event_dict for k in ["symbol", "price"]):
                logger.warning(
                    f"[REPLAY] Underlying event {line_num} missing required fields"
                )
                return None

        elif event_type == "nbbo":
            if not all(
                k in event_dict
                for k in ["symbol", "contract", "strike", "right", "bid", "ask"]
            ):
                logger.warning(
                    f"[REPLAY] NBBO event {line_num} missing required fields"
                )
                return None

        return ReplayEvent(type=event_type, timestamp=float(timestamp), data=event_dict)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    async def play(
        self,
        speed: float = 1.0,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ):
        """
        Play back events through MassiveMux.

        Args:
            speed: Playback speed multiplier
                   1.0 = real-time
                   2.0 = 2x speed
                   0.0 = instant (no delays)
            start_time: Optional start timestamp (skip events before)
            end_time: Optional end timestamp (stop events after)

        Raises:
            RuntimeError: If events not loaded
        """
        if not self.loaded:
            self.load()

        if not self.events:
            logger.warning("[REPLAY] No events to play")
            return

        logger.info(f"[REPLAY] Starting playback (speed={speed}x)...")

        # Filter by time range if specified
        events = self.events
        if start_time is not None:
            events = [e for e in events if e.timestamp >= start_time]
        if end_time is not None:
            events = [e for e in events if e.timestamp <= end_time]

        if not events:
            logger.warning("[REPLAY] No events in specified time range")
            return

        # Playback loop
        start_ts = events[0].timestamp
        replay_start = asyncio.get_event_loop().time()

        for idx, event in enumerate(events):
            # Calculate delay
            if speed > 0:
                # Time since replay start (in replay time)
                replay_elapsed = event.timestamp - start_ts

                # Scale by speed
                scaled_elapsed = replay_elapsed / speed

                # Real time we should be at
                target_time = replay_start + scaled_elapsed

                # Current real time
                current_time = asyncio.get_event_loop().time()

                # Delay if needed
                delay = target_time - current_time
                if delay > 0:
                    await asyncio.sleep(delay)

            # Inject event
            await self._inject_event(event)

            # Progress logging
            if (idx + 1) % 100 == 0:
                progress = ((idx + 1) / len(events)) * 100
                logger.info(
                    f"[REPLAY] Progress: {idx + 1}/{len(events)} ({progress:.1f}%)"
                )

        logger.info(f"[REPLAY] Playback complete: {len(events)} events")

    async def _inject_event(self, event: ReplayEvent):
        """
        Inject single event into MassiveMux.

        Routes to appropriate handler exactly as live WS would.

        Args:
            event: ReplayEvent to inject
        """
        if event.type == "underlying":
            # Normalize to MassiveMux format
            normalized = {
                "symbol": event.data["symbol"],
                "price": event.data["price"],
                "bid": event.data.get("bid"),
                "ask": event.data.get("ask"),
                "_recv_ts": event.timestamp,
            }

            # Inject through mux (same path as live WS)
            await self.mux._handle_underlying(normalized)

        elif event.type == "nbbo":
            # Normalize to MassiveMux format
            normalized = {
                "symbol": event.data["symbol"],
                "contract": event.data["contract"],
                "strike": float(event.data["strike"]),
                "right": event.data["right"],
                "bid": float(event.data["bid"]),
                "ask": float(event.data["ask"]),
                "_recv_ts": event.timestamp,
            }

            # Inject through mux (same path as live WS)
            await self.mux._handle_option(normalized)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        """
        Get replay statistics.

        Returns:
            Dict with stats about loaded events
        """
        if not self.loaded:
            return {"loaded": False}

        symbols = set(e.data.get("symbol") for e in self.events if e.data.get("symbol"))

        return {
            "loaded": True,
            "filepath": str(self.filepath),
            "total_events": self.total_events,
            "underlying_events": self.underlying_events,
            "option_events": self.option_events,
            "duration_seconds": self.duration_seconds,
            "symbols": sorted(symbols),
            "start_time": self.events[0].timestamp if self.events else None,
            "end_time": self.events[-1].timestamp if self.events else None,
        }

    def print_stats(self):
        """Print replay statistics to console."""
        stats = self.get_stats()

        if not stats["loaded"]:
            print("No replay loaded")
            return

        print("\n" + "=" * 60)
        print("REPLAY STATISTICS".center(60))
        print("=" * 60)
        print(f"File: {stats['filepath']}")
        print(f"Total events: {stats['total_events']:,}")
        print(f"  Underlying: {stats['underlying_events']:,}")
        print(f"  Options: {stats['option_events']:,}")
        print(f"Duration: {stats['duration_seconds']:.1f} seconds")
        print(f"Symbols: {', '.join(stats['symbols'])}")
        print("=" * 60 + "\n")


# =====================================================================
# Example Usage
# =====================================================================
async def main():
    """
    Example: Replay historical data through WS-native architecture.

    This demonstrates how to integrate ReplayEngine with:
        • MassiveMux (or SyntheticMux for testing)
        • Orchestrator
        • ExecutionEngine

    The replay events flow through exactly the same path as
    live WebSocket events would.
    """

    # Import here to avoid circular dependencies
    from bot_0dte.orchestrator import Orchestrator
    from bot_0dte.execution.engine import ExecutionEngine
    from bot_0dte.infra.logger import StructuredLogger
    from bot_0dte.infra.telemetry import Telemetry

    # For replay, we use SyntheticMux (no real WS connections)
    from bot_0dte.sim.bot_ws_sim import (
        SyntheticMux,
        SyntheticStocksWS,
        SyntheticOptionsWS,
    )

    print("\n" + "=" * 60)
    print("WS-NATIVE REPLAY ENGINE".center(60))
    print("=" * 60 + "\n")

    # ------------------------------------------------------------------
    # 1. Setup synthetic infrastructure
    # ------------------------------------------------------------------
    print("[SETUP] Creating synthetic WS adapters...")
    stocks_ws = SyntheticStocksWS()
    options_ws = SyntheticOptionsWS()
    mux = SyntheticMux(stocks_ws, options_ws)

    # ------------------------------------------------------------------
    # 2. Setup execution engine (mock mode)
    # ------------------------------------------------------------------
    print("[SETUP] Creating execution engine (mock mode)...")
    engine = ExecutionEngine(use_mock=True)
    await engine.start()

    # ------------------------------------------------------------------
    # 3. Setup orchestrator
    # ------------------------------------------------------------------
    print("[SETUP] Creating orchestrator...")
    orch = Orchestrator(
        engine=engine,
        mux=mux,
        telemetry=Telemetry(),
        logger=StructuredLogger(),
        universe=["SPY"],
        auto_trade_enabled=True,
        trade_mode="shadow",
    )

    print("[SETUP] Starting orchestrator...")
    await orch.start()

    print("\n[SETUP] Complete ✅\n")

    # ------------------------------------------------------------------
    # 4. Create replay engine
    # ------------------------------------------------------------------
    replay_file = "./data/replays/spy_20241122.jsonl"

    print(f"[REPLAY] Loading {replay_file}...")
    replay = ReplayEngine(replay_file, mux, orch)

    try:
        replay.load()
        replay.print_stats()

        # ------------------------------------------------------------------
        # 5. Run replay
        # ------------------------------------------------------------------
        print("[REPLAY] Starting playback at 2x speed...\n")
        await replay.play(speed=2.0)

        print("\n✅ Replay complete!\n")

        # ------------------------------------------------------------------
        # 6. Show results
        # ------------------------------------------------------------------
        print("=" * 60)
        print("REPLAY RESULTS".center(60))
        print("=" * 60)

        # VWAP tracker
        tracker = orch._vwap_tracker.get("SPY")
        if tracker:
            print(f"\n📊 VWAP Tracker:")
            print(f"   Ticks: {len(tracker.prices)}")
            print(f"   Last VWAP: ${tracker.last_vwap:.2f}")

        # Chain aggregator
        chain = orch.chain_agg.get_chain("SPY")
        print(f"\n📈 Chain Aggregator:")
        print(f"   Options cached: {len(chain)}")

        print("\n" + "=" * 60 + "\n")

    except FileNotFoundError:
        print(f"\n❌ Replay file not found: {replay_file}")
        print("\nTo create a replay file, record live events in this format:")
        print(
            """
Example JSONL (spy_20241122.jsonl):
{"type":"underlying","symbol":"SPY","price":450.23,"bid":450.20,"ask":450.25,"_recv_ts":1700000000.123}
{"type":"nbbo","symbol":"SPY","contract":"O:SPY241122C00450000","strike":450,"right":"C","bid":0.95,"ask":1.05,"_recv_ts":1700000000.125}
        """
        )

    except Exception as e:
        print(f"\n❌ Replay failed: {e}")
        import traceback

        traceback.print_exc()

    finally:
        await mux.close()


if __name__ == "__main__":
    """
    Run replay from command line.

    Usage:
        python -m bot_0dte.data.replay.replay_engine

    Or:
        cd bot_0dte/data/replay
        python replay_engine.py
    """
    asyncio.run(main())
