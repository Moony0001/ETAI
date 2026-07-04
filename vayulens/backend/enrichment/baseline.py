"""Clean-day baseline -> excess PM2.5.

The baseline is the low percentile (default 10th) of PM2.5 over a trailing
window. Anything a station reads above its own baseline is treated as
locally-generated *excess* that the attribution engine explains. Using a
low percentile approximates a "clean day" for that location so we don't
attribute the persistent regional floor to local sources.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional

import numpy as np

from backend.config import (
    BASELINE_FALLBACK_UGM3,
    BASELINE_MIN_SAMPLES,
    BASELINE_PERCENTILE,
)
from backend.models import Reading

logger = logging.getLogger("vayulens.enrichment.baseline")


def percentile_baseline(
    values: Iterable[float],
    percentile: float = BASELINE_PERCENTILE,
    min_samples: int = BASELINE_MIN_SAMPLES,
) -> Optional[float]:
    """Low-percentile baseline from a series of concentrations."""
    arr = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if arr.size < min_samples:
        return None
    return float(np.percentile(arr, percentile))


def station_baseline(
    history: list[Reading],
    parameter: str = "pm25",
    percentile: float = BASELINE_PERCENTILE,
) -> float:
    """Baseline for one station from its trailing history (fallback if sparse)."""
    vals = [r.value for r in history if r.parameter == parameter]
    base = percentile_baseline(vals, percentile=percentile)
    if base is None:
        logger.debug("[baseline] insufficient history (%d pts) — using fallback", len(vals))
        return BASELINE_FALLBACK_UGM3
    return base


def excess(pm25_obs: float, pm25_baseline: float) -> float:
    """excess = max(0, obs - baseline)."""
    return max(0.0, pm25_obs - pm25_baseline)


def regional_floor(
    station_excesses: dict[str, float], percentile: float = 25.0
) -> float:
    """The spatial common-mode: excess present across (almost) all stations.

    We take a low percentile of the per-station excess at time t. Whatever
    excess is shared by nearly every station is regional background rather than
    any single ward's local source.
    """
    vals = [v for v in station_excesses.values() if v is not None and np.isfinite(v)]
    if not vals:
        return 0.0
    return float(np.percentile(vals, percentile))
