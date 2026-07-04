"""Short-horizon PM2.5 forecast — persistence baseline (real) + LightGBM stub.

Forecasting is *supporting*, not the core deliverable. The persistence and
diurnal-persistence baselines below actually run; the LightGBM path is a wired
placeholder with a clear TODO so the ML story is easy to finish later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

logger = logging.getLogger("vayulens.forecast")


def persistence_forecast(
    series: list[tuple[datetime, float]], horizon_h: int = 6
) -> list[tuple[datetime, float]]:
    """Naive persistence: next `horizon_h` hours = last observed value."""
    if not series:
        return []
    series = sorted(series, key=lambda x: x[0])
    t_last, v_last = series[-1]
    return [(t_last + timedelta(hours=h), v_last) for h in range(1, horizon_h + 1)]


def diurnal_persistence_forecast(
    series: list[tuple[datetime, float]], horizon_h: int = 6
) -> list[tuple[datetime, float]]:
    """Persistence blended toward the same hour-of-day's recent average.

    Usually beats plain persistence during morning/evening ramps.
    """
    if len(series) < 24:
        return persistence_forecast(series, horizon_h)
    series = sorted(series, key=lambda x: x[0])
    by_hour: dict[int, list[float]] = {}
    for t, v in series:
        by_hour.setdefault(t.hour, []).append(v)
    hour_mean = {h: float(np.mean(vs)) for h, vs in by_hour.items()}
    t_last, v_last = series[-1]
    out: list[tuple[datetime, float]] = []
    for h in range(1, horizon_h + 1):
        ts = t_last + timedelta(hours=h)
        clim = hour_mean.get(ts.hour, v_last)
        # decay from persistence toward climatology as horizon grows
        w = min(1.0, h / horizon_h)
        out.append((ts, (1 - w) * v_last + w * clim))
    return out


class LightGBMForecaster:
    """STUB — gradient-boosted PM2.5 nowcast.

    TODO: assemble a feature frame (lagged PM2.5, wind_speed/dir, BLH, RH,
    hour-of-day, upwind fire score, day-of-week) and train a LightGBM regressor
    per horizon. Until then `predict` falls back to diurnal persistence so the
    pipeline/API keep working.
    """

    def __init__(self) -> None:
        self.model = None
        self._trained = False

    def train(self, X: "np.ndarray", y: "np.ndarray") -> None:  # noqa: F821
        # TODO: import lightgbm; lgb.LGBMRegressor(...).fit(X, y)
        logger.info("[forecast] LightGBM training not implemented yet (stub).")
        self._trained = False

    def predict(
        self, series: list[tuple[datetime, float]], horizon_h: int = 6
    ) -> list[tuple[datetime, float]]:
        if not self._trained:
            return diurnal_persistence_forecast(series, horizon_h)
        raise NotImplementedError  # TODO: real inference once trained
