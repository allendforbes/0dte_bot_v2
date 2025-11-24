"""
VWAP Tracker (rolling window calculation)
"""


class VWAPTracker:
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.prices = []
        self.volumes = []
        self.last_vwap = None
        self.last_dev = 0.0

    def update(self, price: float, volume: float = 1.0):
        self.prices.append(price)
        self.volumes.append(volume)

        if len(self.prices) > self.window_size:
            self.prices.pop(0)
            self.volumes.pop(0)

        total_pv = sum(p * v for p, v in zip(self.prices, self.volumes))
        total_v = sum(self.volumes)
        vwap = total_pv / total_v if total_v > 0 else price

        vwap_dev = price - vwap
        vwap_dev_change = vwap_dev - self.last_dev

        self.last_vwap = vwap
        self.last_dev = vwap_dev

        return {
            "vwap": vwap,
            "vwap_dev": vwap_dev,
            "vwap_dev_change": vwap_dev_change,
        }
