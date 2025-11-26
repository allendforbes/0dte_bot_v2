"""
StructuredLogger v2.0 — Async, Colorized, Filterable, Pretty Output

Features Added:
    ✓ Color-coded log levels
    ✓ Async file batching (high-speed JSONL writer)
    ✓ Pretty tabular console output
    ✓ Log filtering (allowlist on levels + events)

Safe for:
    • Orchestrator
    • MUX
    • ContractEngine
    • SIM replay
    • High-frequency event loads
"""

import os
import json
import time
import asyncio
from datetime import datetime
from typing import Optional, List


# ------------------------------------------------------------
# ANSI Colors
# ------------------------------------------------------------
class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"

    # Levels
    INFO = "\033[36m"    # cyan
    EVENT = "\033[32m"   # green
    WARN = "\033[33m"    # yellow
    ERROR = "\033[31m"   # red
    DEBUG = "\033[90m"   # dim grey

    # Table + Prefix
    PREFIX = "\033[35m"  # magenta
    COMPONENT = "\033[34m"  # blue


# ------------------------------------------------------------
# Structured Logger
# ------------------------------------------------------------
class StructuredLogger:
    def __init__(
        self,
        log_dir: str = "logs",
        filename_prefix: str = "run",
        prefix: Optional[str] = None,
        component: Optional[str] = None,
        # NEW features:
        level_filter: Optional[List[str]] = None,    # ["EVENT", "INFO"]
        event_filter: Optional[List[str]] = None,    # ["signal_generated"]
        table: bool = True,                          # pretty console view
    ):
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(log_dir, f"{filename_prefix}_{ts}.jsonl")

        # Cosmetic
        self.prefix = prefix
        self.component = component
        self.table = table

        # Filters
        self.level_filter = set(level_filter) if level_filter else None
        self.event_filter = set(event_filter) if event_filter else None

        # Async writer
        self._queue = asyncio.Queue()
        self._writer_task = asyncio.create_task(self._writer())

    # ============================================================
    # Async file writer — batching for high throughput
    # ============================================================
    async def _writer(self):
        """Asynchronous batch writer to avoid blocking on every log."""
        BATCH_SIZE = 25
        INTERVAL = 0.15  # 150ms

        buffer = []
        while True:
            try:
                # Get 1 item (wait)
                rec = await self._queue.get()
                buffer.append(rec)

                # Pull more items without waiting
                for _ in range(BATCH_SIZE - 1):
                    try:
                        rec = self._queue.get_nowait()
                        buffer.append(rec)
                    except asyncio.QueueEmpty:
                        break

                # Write batch
                with open(self.path, "a") as f:
                    for item in buffer:
                        f.write(json.dumps(item) + "\n")
                buffer.clear()

                await asyncio.sleep(INTERVAL)

            except Exception as e:
                print(f"[LOGGER ERROR] writer crashed: {e}")
                await asyncio.sleep(1)

    # ============================================================
    # Building a unified prefix for console logs
    # ============================================================
    def _fmt_prefix(self):
        if self.prefix and self.component:
            return f"{Color.PREFIX}{self.prefix}/{Color.COMPONENT}{self.component}{Color.RESET}"
        if self.prefix:
            return f"{Color.PREFIX}{self.prefix}{Color.RESET}"
        if self.component:
            return f"{Color.COMPONENT}{self.component}{Color.RESET}"
        return ""

    # ============================================================
    # Console color mapping
    # ============================================================
    def _color_for(self, level: str):
        return {
            "INFO": Color.INFO,
            "EVENT": Color.EVENT,
            "WARN": Color.WARN,
            "ERROR": Color.ERROR,
            "DEBUG": Color.DEBUG,
        }.get(level, Color.RESET)

    # ============================================================
    # Pretty printing
    # ============================================================
    def _print(self, level: str, event: str, payload):
        prefix = self._fmt_prefix()
        color = self._color_for(level)

        if self.table:
            print(
                f"{prefix} "
                f"{color}{level:<6}{Color.RESET} "
                f"{event:<22} "
                f"{json.dumps(payload) if payload else ''}"
            )
        else:
            print(f"{prefix} [{level}] {event} :: {payload if payload else ''}")

    # ============================================================
    # Emit (main entry)
    # ============================================================
    def _emit(self, level: str, event: str, payload=None):
        # ---------- Filtering ----------
        if self.level_filter and level not in self.level_filter:
            return
        if self.event_filter and event not in self.event_filter:
            return

        # ---------- JSON record ----------
        record = {
            "ts": time.time(),
            "iso": datetime.utcnow().isoformat(),
            "level": level,
            "event": event,
        }
        if self.component:
            record["component"] = self.component
        if payload is not None:
            record["data"] = payload

        # Queue for async writer
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            pass  # safety

        # ---------- Console output ----------
        if level != "DEBUG":  # keep debug quiet
            self._print(level, event, payload)

    # ============================================================
    # Public API
    # ============================================================
    def info(self, event, payload=None):
        self._emit("INFO", event, payload)

    def warn(self, event, payload=None):
        self._emit("WARN", event, payload)

    def error(self, event, payload=None):
        self._emit("ERROR", event, payload)

    def debug(self, payload):
        self._emit("DEBUG", "debug", payload)

    def log_event(self, event, payload=None):
        """Primary orchestrator entrypoint."""
        self._emit("EVENT", event, payload)
