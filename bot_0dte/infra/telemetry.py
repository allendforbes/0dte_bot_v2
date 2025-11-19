"""
Telemetry — ultra-lightweight timing utilities for latency + performance.

Used by:
    • LatencyPrecheck
    • StrikeSelector timing
    • Execution timing
    • Orchestrator performance diagnostics
"""

import time


class Telemetry:
    # ------------------------------------------
    @staticmethod
    def now():
        """Return high-precision timestamp."""
        return time.perf_counter()

    # ------------------------------------------
    @staticmethod
    def elapsed_ms(t0):
        """Return elapsed milliseconds since t0."""
        return (time.perf_counter() - t0) * 1000

    # ------------------------------------------
    def profile_block(self, label: str):
        """
        Context manager profiler:

            with telemetry.profile_block("strike_select"):
                ...
        """
        class _Block:
            def __init__(self, outer, label):
                self.outer = outer
                self.label = label
                self.t0 = None

            def __enter__(self):
                self.t0 = time.perf_counter()

            def __exit__(self, exc_type, exc, tb):
                dt = (time.perf_counter() - self.t0) * 1000
                print(f"[PROFILE] {self.label}: {dt:.2f} ms")

        return _Block(self, label)

