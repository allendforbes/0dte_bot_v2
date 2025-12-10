class FreshnessTracker:
    def __init__(self):
        self.last_ts_ms = None

    def update(self, ts_ms: int):
        self.last_ts_ms = ts_ms

    def is_fresh(self, now_ms: int, max_age_ms: int) -> bool:
        if self.last_ts_ms is None:
            return False
        return (now_ms - self.last_ts_ms) <= max_age_ms
