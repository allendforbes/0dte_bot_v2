import time

class Telemetry:
    """
    Ultra-lightweight timing + latency holder.
    In production:
        • latency_ms unused (None)
    In simulation:
        • SyntheticMux may set telemetry.latency_ms[symbol] = float
    """

    def __init__(self):
        # Per-symbol latency map (sim only)
        self._latency_map = {}

    # ------------------------------------------------------------------
    # High-precision timers
    # ------------------------------------------------------------------
    @staticmethod
    def now():
        return time.perf_counter()

    @staticmethod
    def elapsed_ms(t0):
        return (time.perf_counter() - t0) * 1000

    # ------------------------------------------------------------------
    # Latency accessors (safe for prod & sim)
    # ------------------------------------------------------------------
    @property
    def latency_ms(self):
        """
        Access latency map.
        Orchestrator always assumes: telemetry.latency_ms.get(symbol)
        """
        return self._latency_map

    @latency_ms.setter
    def latency_ms(self, value):
        """
        Simulation may assign a float → convert to uniform map format.
        If value is a float → treat as DEFAULT latency for all symbols.
        If value is a dict → assign directly.
        """
        if isinstance(value, dict):
            self._latency_map = value
        else:
            # convert scalar → default for all symbols
            try:
                v = float(value)
                self._latency_map = {"DEFAULT": v}
            except Exception:
                self._latency_map = {}

    # ------------------------------------------------------------------
    # Profiling block
    # ------------------------------------------------------------------
    def profile_block(self, label: str):
        class _Block:
            def __init__(self, label):
                self.label = label
                self.t0 = None

            def __enter__(self):
                self.t0 = time.perf_counter()

            def __exit__(self, exc_type, exc, tb):
                dt = (time.perf_counter() - self.t0) * 1000
                print(f"[PROFILE] {self.label}: {dt:.2f} ms")

        return _Block(label)
