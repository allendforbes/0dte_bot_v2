"""
StructuredLogger — minimal structured event logger.

Features:
    • JSONL event stream for auditability
    • Pretty console output
    • Lightweight and safe for async usage
"""

import json
import time
import os
from datetime import datetime


class StructuredLogger:
    def __init__(self, log_dir="logs", filename_prefix="run"):
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(log_dir, f"{filename_prefix}_{ts}.jsonl")

    # ------------------------------------------------------------------
    def _emit(self, level, event, payload=None):
        record = {
            "ts": time.time(),
            "iso": datetime.utcnow().isoformat(),
            "level": level,
            "event": event,
        }
        if payload is not None:
            record["data"] = payload

        # write to file
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

        # console output (human-friendly)
        if level != "DEBUG":  # keep debug quiet unless needed
            print(f"[{level}] {event} :: {payload if payload else ''}")

    # ------------------------------------------------------------------
    def info(self, event, payload=None):
        self._emit("INFO", event, payload)

    def warn(self, event, payload=None):
        self._emit("WARN", event, payload)

    def error(self, event, payload=None):
        self._emit("ERROR", event, payload)

    def debug(self, payload):
        # dedicated debug channel
        self._emit("DEBUG", "debug", payload)

    # ------------------------------------------------------------------
    def log_event(self, event, payload=None):
        """Primary entrypoint for strategy + orchestrator logs."""
        self._emit("EVENT", event, payload)

