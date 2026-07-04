"""Feature engineering: turn raw fused data into normalised evidence signals.

Every signal the fingerprints reference is computed here and squashed to [0, 1]
so the attribution weights in config.py are directly comparable and the whole
thing stays inspectable. Nothing here hard-codes a threshold — they all come
from backend.config.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from backend.config import (
    BIOMASS_OFFSEASON_FACTOR,
    BIOMASS_PEAK_MONTHS,
    BIOMASS_SHOULDER_MONTHS,
    COARSE_RATIO_DUST_HI,
    COARSE_RATIO_DUST_LO,
    COARSE_RATIO_FINE_HI,
    COARSE_RATIO_FINE_LO,
    DAYTIME_PEAK,
    FIRE_SCORE_HALF,
    RH_DUST_LO,
    RH_DUST_MAX,
    RUSH_EVENING,
    RUSH_MORNING,
    STEADY_CV_REF,
    Z_CLIP,
)
from backend.models import MetPoint, TropomiPoint

logger = logging.getLogger("vayulens.enrichment.features")

IST_OFFSET_H = 5.5  # Asia/Kolkata, for diurnal shape


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def zscore_feature(value: Optional[float], mean: float, std: float) -> float:
    """(value-mean)/std, clipped to [0, Z_CLIP], scaled to [0,1]. Absent -> 0."""
    if value is None or std is None or std <= 1e-6:
        return 0.0
    z = (value - mean) / std
    return _clamp(z, 0.0, Z_CLIP) / Z_CLIP


def saturate(x: float, half: float) -> float:
    return 0.0 if x <= 0 else x / (x + half)


def local_hour(t: datetime) -> float:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (t.astimezone(timezone.utc).hour + t.minute / 60.0 + IST_OFFSET_H) % 24.0


def _rush_template() -> np.ndarray:
    tmpl = np.zeros(24)
    for a, b in (RUSH_MORNING, RUSH_EVENING):
        for h in range(a, b):
            tmpl[h % 24] = 1.0
    return tmpl


def diurnal_profile(series: list[tuple[datetime, float]]) -> Optional[np.ndarray]:
    """Mean concentration by local hour-of-day (24-vector), or None if too sparse."""
    if len(series) < 8:
        return None
    buckets: list[list[float]] = [[] for _ in range(24)]
    for t, v in series:
        if v is None or not math.isfinite(v):
            continue
        buckets[int(local_hour(t))].append(v)
    prof = np.array([np.mean(b) if b else np.nan for b in buckets])
    if np.isnan(prof).sum() > 16:  # need at least ~8 hours covered
        return None
    # fill gaps with overall mean so correlation is defined
    prof = np.where(np.isnan(prof), np.nanmean(prof), prof)
    return prof


def rush_fit(series: list[tuple[datetime, float]]) -> float:
    """Correlation of the diurnal profile with a rush-hour template -> [0,1]."""
    prof = diurnal_profile(series)
    if prof is None:
        return 0.0
    tmpl = _rush_template()
    if prof.std() < 1e-6:
        return 0.0
    r = float(np.corrcoef(prof, tmpl)[0, 1])
    return _clamp(r, 0.0, 1.0)


def steadiness(series: list[tuple[datetime, float]]) -> float:
    """1 - coefficient-of-variation/ref, over the window -> [0,1]. Flat => 1."""
    vals = np.array([v for _, v in series if v is not None and math.isfinite(v)])
    if vals.size < 6 or vals.mean() <= 1e-6:
        return 0.0
    cv = vals.std() / vals.mean()
    return _clamp(1.0 - cv / STEADY_CV_REF)


def daytime_factor(t: datetime) -> float:
    """Smooth midday bump for daytime re-suspension -> [0,1]."""
    h = local_hour(t)
    lo, hi = DAYTIME_PEAK
    center = (lo + hi) / 2.0
    half_width = (hi - lo) / 2.0 + 3.0  # taper 3h beyond the plateau
    if lo <= h <= hi:
        return 1.0
    return _clamp(1.0 - abs(h - center) / half_width)


def seasonal_biomass(t: datetime) -> float:
    m = t.month
    if m in BIOMASS_PEAK_MONTHS:
        return 1.0
    if m in BIOMASS_SHOULDER_MONTHS:
        return 0.6
    return BIOMASS_OFFSEASON_FACTOR


def low_rh_factor(rh: Optional[float]) -> float:
    if rh is None:
        return 0.0
    return _clamp((RH_DUST_MAX - rh) / (RH_DUST_MAX - RH_DUST_LO))


def coarse_ratio(pm10: Optional[float], pm25: Optional[float]) -> Optional[float]:
    if not pm10 or not pm25 or pm25 <= 1e-6:
        return None
    return max(1.0, pm10 / pm25)  # PM10 includes PM2.5, so ratio >= 1


def coarse_ratio_dust(ratio: Optional[float]) -> float:
    if ratio is None:
        return 0.0
    return _clamp((ratio - COARSE_RATIO_DUST_LO) / (COARSE_RATIO_DUST_HI - COARSE_RATIO_DUST_LO))


def coarse_ratio_fine(ratio: Optional[float]) -> float:
    if ratio is None:
        return 0.0
    return _clamp((COARSE_RATIO_FINE_HI - ratio) / (COARSE_RATIO_FINE_HI - COARSE_RATIO_FINE_LO))


# ---------------------------------------------------------------------------
@dataclass
class FeatureInputs:
    t: datetime
    ward_values: dict[str, float]                 # param -> ward representative conc
    panel_stats: dict[str, tuple[float, float]]   # param -> (mean, std) across stations
    met: Optional[MetPoint]
    window_pm25: list[tuple[datetime, float]]     # ward PM2.5 recent hourly series
    fire_score_raw: float
    proximity: dict
    ward_excess: float
    regional_floor: float
    tropomi: Optional[TropomiPoint] = None


@dataclass
class FeatureBundle:
    features: dict[str, float] = field(default_factory=dict)   # normalised [0,1]
    raw: dict = field(default_factory=dict)                    # inspectable raw values
    present: dict[str, bool] = field(default_factory=dict)     # input availability


def build_features(inp: FeatureInputs) -> FeatureBundle:
    """Compute the full normalised feature vector for one ward at one time."""
    v = inp.ward_values
    stats = inp.panel_stats

    def zf(param: str) -> float:
        m, s = stats.get(param, (0.0, 0.0))
        return zscore_feature(v.get(param), m, s)

    ratio = coarse_ratio(v.get("pm10"), v.get("pm25"))
    rh = inp.met.rh if inp.met else None

    # CO/NO2/SO2 z from ground; UVAI from satellite (0 when absent)
    co_z = zf("co")
    uvai_z = 0.0
    if inp.tropomi and inp.tropomi.uvai is not None:
        # single-point UVAI: map typical smoke range [0, 3] -> [0,1]
        uvai_z = _clamp(inp.tropomi.uvai / 3.0)

    common_mode = 0.0
    if inp.ward_excess > 1e-6:
        common_mode = _clamp(inp.regional_floor / inp.ward_excess)

    prox = inp.proximity or {}

    features = {
        # biomass
        "uvai_z": uvai_z,
        "co_z": co_z,
        "upwind_fire": saturate(inp.fire_score_raw, FIRE_SCORE_HALF),
        "coarse_ratio_fine": coarse_ratio_fine(ratio),
        "seasonal_biomass": seasonal_biomass(inp.t),
        # traffic
        "no2_z": zf("no2"),
        "rush_fit": rush_fit(inp.window_pm25),
        "road_proximity": float(prox.get("road_proximity", 0.0)),
        # dust
        "coarse_ratio_dust": coarse_ratio_dust(ratio),
        "low_rh": low_rh_factor(rh),
        "construction_proximity": float(prox.get("construction_proximity", 0.0)),
        "daytime": daytime_factor(inp.t),
        # industrial
        "so2_z": zf("so2"),
        "stack_proximity": float(prox.get("stack_proximity", 0.0)),
        "steady": steadiness(inp.window_pm25),
        # regional
        "common_mode": common_mode,
    }

    raw = {
        "coarse_ratio": round(ratio, 2) if ratio else None,
        "rh": rh,
        "fire_score_raw": round(inp.fire_score_raw, 2),
        "ward_excess": round(inp.ward_excess, 2),
        "regional_floor": round(inp.regional_floor, 2),
        "local_hour": round(local_hour(inp.t), 1),
        "proximity_source": prox.get("source"),
        "uvai": inp.tropomi.uvai if inp.tropomi else None,
    }

    present = {
        "pm25": v.get("pm25") is not None,
        "pm10": v.get("pm10") is not None,
        "no2": v.get("no2") is not None,
        "so2": v.get("so2") is not None,
        "co": v.get("co") is not None,
        "wind": inp.met is not None,
        "fires": inp.fire_score_raw > 0.0,
        "geo": bool(prox),
    }

    return FeatureBundle(features=features, raw=raw, present=present)
