from time import monotonic

class ChainFreshnessV2:
    """
    Freshness control ensuring:
    - adaptive thresholding (open vs normal)
    - hydration after reconnect (N consecutive frames)
    - dual timers: frame age + hb age
    """

    def __init__(self):
        self.thresh_open_ms = 250
        self.thresh_normal_ms = 120
        self.required_frames = 3

        self.last_frame_monotonic = 0
        self.last_hb_monotonic = 0
        self.consec = 0

    # -------------------------------------
    # FEED UPDATES
    # -------------------------------------
    def update_frame(self):
        self.last_frame_monotonic = monotonic()

    def update_heartbeat(self):
        self.last_hb_monotonic = monotonic()

    # -------------------------------------
    # FRESHNESS CHECK
    # -------------------------------------
    def is_fresh(self, *, is_open_window: bool) -> bool:
        now = monotonic()

        frame_age_ms = (now - self.last_frame_monotonic) * 1000
        hb_age_ms    = (now - self.last_hb_monotonic) * 1000

        thresh = self.thresh_open_ms if is_open_window else self.thresh_normal_ms

        # fail if both frame + heartbeat stale
        if frame_age_ms > thresh or hb_age_ms > thresh * 2:
            self.consec = 0
            return False

        self.consec += 1
        return self.consec >= self.required_frames

    # -------------------------------------
    # VISIBILITY
    # -------------------------------------
    def frame_age_ms(self):
        return (monotonic() - self.last_frame_monotonic) * 1000

    def hb_age_ms(self):
        return (monotonic() - self.last_hb_monotonic) * 1000
