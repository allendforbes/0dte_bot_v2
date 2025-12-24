# bot_0dte/infra/ui_clock.py
import time

class UiClock:
    def __init__(self, hz: float = 5.0) -> None:
        self.period = 1.0 / float(hz)
        self._next = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now >= self._next:
            self._next = now + self.period
            return True
        return False