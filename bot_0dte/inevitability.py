import json
import csv
import os
import math
import datetime as dt

TARGET_DAILY_RETURN = 0.014
TOP_UP_AT = 10000
TOP_UP_AMOUNT = 5000
FORECAST_DAYS = 180
RISK_PER_TRADE = 0.01


class InevitabilityEngine:
    def __init__(self, start_equity=5000):
        self.equity = start_equity
        self.realized_curve = []
        self.projected_curve = self._build_projection(start_equity)
        self.variance_upper, self.variance_lower = self._build_variance_bands()
        self.top_up_applied = False

        os.makedirs("./equity", exist_ok=True)

    # --------------------------------------------------------------
    # INTERNAL PROJECTIONS
    # --------------------------------------------------------------
    def _build_projection(self, start):
        eq = start
        proj = []
        for _ in range(FORECAST_DAYS):
            eq *= (1 + TARGET_DAILY_RETURN)
            proj.append(eq)
        return proj

    def _build_variance_bands(self):
        upper = []
        lower = []
        eq = self.projected_curve[0] / (1 + TARGET_DAILY_RETURN)

        for val in self.projected_curve:
            upper.append(val * 1.10)  # +10% envelope
            lower.append(val * 0.90)  # -10% envelope
        return upper, lower

    # --------------------------------------------------------------
    # DAILY UPDATE FROM LIVE BOT
    # --------------------------------------------------------------
    def update(self, pnl_today):
        """Called end of each trading day. pnl_today is realized USD."""
        self.equity += pnl_today

        # top-up logic
        if not self.top_up_applied and self.equity >= TOP_UP_AT:
            self.equity += TOP_UP_AMOUNT
            self.top_up_applied = True

        # realized curve update
        self.realized_curve.append(self.equity)

    # --------------------------------------------------------------
    # SAVE OUTPUT
    # --------------------------------------------------------------
    def save_checkpoint(self):
        data = {
            "realized": self.realized_curve,
            "projected": self.projected_curve,
            "upper": self.variance_upper,
            "lower": self.variance_lower,
            "top_up_applied": self.top_up_applied,
            "updated": dt.datetime.now().isoformat(),
        }

        with open("./equity/0dte_inevitability.json", "w") as f:
            json.dump(data, f, indent=2)

        with open("./equity/0dte_inevitability.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["day", "realized", "projected", "upper", "lower"])
            for i in range(FORECAST_DAYS):
                r = self.realized_curve[i] if i < len(self.realized_curve) else ""
                p = self.projected_curve[i]
                u = self.variance_upper[i]
                l = self.variance_lower[i]
                w.writerow([i + 1, r, p, u, l])

    # --------------------------------------------------------------
    # ASCII VISUALIZATION
    # --------------------------------------------------------------
    def ascii_projection(self, out_path="./equity/ascii_projection.txt"):
        MAX_HEIGHT = 50  # characters
        MAX_VALUE = max(self.variance_upper)

        def scale(v):
            return int((v / MAX_VALUE) * MAX_HEIGHT)

        lines = []

        for i in range(FORECAST_DAYS):
            p = scale(self.projected_curve[i])
            u = scale(self.variance_upper[i])
            l = scale(self.variance_lower[i])
            r = scale(self.realized_curve[i]) if i < len(self.realized_curve) else None

            row = [" "] * (MAX_HEIGHT + 2)

            row[p] = "I"  # projected
            row[u] = "V"  # upper
            row[l] = "V"  # lower
            if r is not None:
                row[r] = "O"  # actual

            lines.append("".join(row))

        with open(out_path, "w") as f:
            f.write("\n".join(lines))
