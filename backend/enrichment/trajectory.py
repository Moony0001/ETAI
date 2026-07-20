"""Lagrangian back-trajectory + upwind biomass-fire evidence.

The demo money-shot: from a Delhi ward, step an air parcel BACKWARD hour by
hour along the wind field. If that path sweeps over active fires (typically the
Punjab/Haryana stubble belt to the NW), we have physical evidence that smoke was
advected into the ward.

    dx, dy = -u * dt * 3600, -v * dt * 3600     # metres, opposite the wind vector
    advance lat/lon by (dx, dy); repeat for `hours`.

biomass_evidence(path, fires):
    sum_fires  frp * exp(-dist_to_path_km / 50) * exp(-age_hours / 12)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np

from backend.config import (
    FIRE_KERNEL_AGE_H,
    FIRE_KERNEL_DIST_KM,
    FIRE_MAX_DIST_KM,
    TRAJECTORY_DT_H,
    TRAJECTORY_HOURS,
)
from backend.models import Fire, MetPoint, TrajectoryStep

_EARTH_R_KM = 6371.0088

logger = logging.getLogger("vayulens.enrichment.trajectory")

WindFn = Callable[[float, float, datetime], Optional[MetPoint]]

_M_PER_DEG_LAT = 111_320.0


def _meters_to_degrees(dx_m: float, dy_m: float, lat: float) -> tuple[float, float]:
    dlat = dy_m / _M_PER_DEG_LAT
    dlon = dx_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return dlat, dlon


def back_trajectory(
    lat: float,
    lon: float,
    t0: datetime,
    wind_fn: WindFn,
    hours: int = TRAJECTORY_HOURS,
    dt: int = TRAJECTORY_DT_H,
) -> list[TrajectoryStep]:
    """Step backward `hours` hours from (lat, lon, t0) along the sampled wind."""
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    path: list[TrajectoryStep] = [
        TrajectoryStep(hour_back=0, lat=lat, lon=lon, timestamp=t0)
    ]
    cur_lat, cur_lon, cur_t = lat, lon, t0
    for h in range(1, hours + 1):
        met = wind_fn(cur_lat, cur_lon, cur_t)
        if met is None:
            logger.debug("[trajectory] no wind at step %d — stopping path early.", h)
            break
        dx = -met.u * dt * 3600.0
        dy = -met.v * dt * 3600.0
        dlat, dlon = _meters_to_degrees(dx, dy, cur_lat)
        cur_lat += dlat
        cur_lon += dlon
        cur_t = cur_t - timedelta(hours=dt)
        path.append(
            TrajectoryStep(hour_back=h, lat=cur_lat, lon=cur_lon, timestamp=cur_t)
        )
    return path


def _min_dist_to_path_km(
    flat: np.ndarray, flon: np.ndarray, plat: np.ndarray, plon: np.ndarray
) -> np.ndarray:
    """Min great-circle distance (km) from each fire to the path, vectorised.

    fires (F,) x path nodes (N,) -> per-fire minimum (F,). Vectorised because the
    archive returns thousands of stubble detections and the batch runs this per
    ward; a scalar double loop would dominate runtime.
    """
    flat_r = np.radians(flat)[:, None]
    flon_r = np.radians(flon)[:, None]
    plat_r = np.radians(plat)[None, :]
    plon_r = np.radians(plon)[None, :]
    dphi = plat_r - flat_r
    dlmb = plon_r - flon_r
    a = np.sin(dphi / 2.0) ** 2 + np.cos(flat_r) * np.cos(plat_r) * np.sin(dlmb / 2.0) ** 2
    return (2.0 * _EARTH_R_KM * np.arcsin(np.sqrt(a))).min(axis=1)


def biomass_evidence(
    path: list[TrajectoryStep],
    fires: list[Fire],
    t0: Optional[datetime] = None,
    with_contributors: bool = True,
) -> tuple[float, list[dict]]:
    """Total upwind-fire score + the list of contributing fires (with weights).

    Each fire contributes frp * exp(-dist/50km) * exp(-age/12h). Fires far from
    the path (> FIRE_MAX_DIST_KM) are ignored. A fire *newer* than the analysis
    time (negative age) is a temporal leak — it is discarded, not clamped to age
    0 (which used to hand a future-dated detection the maximum recency weight).

    `with_contributors=False` skips building the per-fire dicts (the batch only
    needs the scalar score) — cheap on a 2,700-cell sweep over thousands of fires.
    """
    if not path or not fires:
        return 0.0, []
    ref_t = t0 or path[0].timestamp
    if ref_t.tzinfo is None:
        ref_t = ref_t.replace(tzinfo=timezone.utc)

    n = len(fires)
    flat = np.fromiter((f.lat for f in fires), dtype=float, count=n)
    flon = np.fromiter((f.lon for f in fires), dtype=float, count=n)
    frp = np.fromiter((f.frp for f in fires), dtype=float, count=n)
    plat = np.fromiter((s.lat for s in path), dtype=float, count=len(path))
    plon = np.fromiter((s.lon for s in path), dtype=float, count=len(path))

    dmin = _min_dist_to_path_km(flat, flon, plat, plon)
    ages = np.fromiter(
        (
            (
                ref_t
                - (f.timestamp if f.timestamp.tzinfo else f.timestamp.replace(tzinfo=timezone.utc))
            ).total_seconds()
            / 3600.0
            for f in fires
        ),
        dtype=float,
        count=n,
    )

    keep = (dmin <= FIRE_MAX_DIST_KM) & (ages >= 0.0)  # discard future-dated fires
    weights = np.where(
        keep,
        frp * np.exp(-dmin / FIRE_KERNEL_DIST_KM) * np.exp(-ages / FIRE_KERNEL_AGE_H),
        0.0,
    )
    total = float(weights[weights > 0.0].sum())

    if not with_contributors:
        return total, []

    contributors: list[dict] = []
    for i in np.nonzero(weights > 0.0)[0]:
        contributors.append(
            {
                "lat": fires[i].lat,
                "lon": fires[i].lon,
                "frp": fires[i].frp,
                "dist_km": round(float(dmin[i]), 1),
                "age_h": round(float(ages[i]), 1),
                "weight": round(float(weights[i]), 2),
            }
        )
    contributors.sort(key=lambda c: c["weight"], reverse=True)
    return total, contributors


# ---------------------------------------------------------------------------
# GeoJSON export for the /trajectory endpoint (the demo visual)
# ---------------------------------------------------------------------------
def trajectory_geojson(
    path: list[TrajectoryStep], contributors: list[dict]
) -> dict:
    """FeatureCollection: the back-trajectory LineString + contributing fires."""
    features: list[dict] = []
    if path:
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "trajectory", "hours": len(path) - 1},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[s.lon, s.lat] for s in path],
                },
            }
        )
        # nodes (with timestamps) for tooltips
        for s in path:
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "node",
                        "hour_back": s.hour_back,
                        "timestamp": s.timestamp.isoformat(),
                    },
                    "geometry": {"type": "Point", "coordinates": [s.lon, s.lat]},
                }
            )
    for c in contributors:
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "fire", **c},
                "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
            }
        )
    return {"type": "FeatureCollection", "features": features}
