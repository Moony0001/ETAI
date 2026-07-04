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

from backend.adapters.geodata import haversine_km
from backend.config import (
    FIRE_KERNEL_AGE_H,
    FIRE_KERNEL_DIST_KM,
    FIRE_MAX_DIST_KM,
    TRAJECTORY_DT_H,
    TRAJECTORY_HOURS,
)
from backend.models import Fire, MetPoint, TrajectoryStep

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


def _min_dist_to_path_km(fire: Fire, path: list[TrajectoryStep]) -> float:
    return min(haversine_km(fire.lat, fire.lon, s.lat, s.lon) for s in path)


def biomass_evidence(
    path: list[TrajectoryStep],
    fires: list[Fire],
    t0: Optional[datetime] = None,
) -> tuple[float, list[dict]]:
    """Total upwind-fire score + the list of contributing fires (with weights).

    Each fire contributes frp * exp(-dist/50km) * exp(-age/12h). Fires far from
    the path (> FIRE_MAX_DIST_KM) are ignored for performance.
    """
    if not path:
        return 0.0, []
    ref_t = t0 or path[0].timestamp
    if ref_t.tzinfo is None:
        ref_t = ref_t.replace(tzinfo=timezone.utc)

    total = 0.0
    contributors: list[dict] = []
    for fire in fires:
        dist = _min_dist_to_path_km(fire, path)
        if dist > FIRE_MAX_DIST_KM:
            continue
        ftime = fire.timestamp
        if ftime.tzinfo is None:
            ftime = ftime.replace(tzinfo=timezone.utc)
        age_h = max(0.0, (ref_t - ftime).total_seconds() / 3600.0)
        weight = (
            fire.frp
            * math.exp(-dist / FIRE_KERNEL_DIST_KM)
            * math.exp(-age_h / FIRE_KERNEL_AGE_H)
        )
        if weight <= 0:
            continue
        total += weight
        contributors.append(
            {
                "lat": fire.lat,
                "lon": fire.lon,
                "frp": fire.frp,
                "dist_km": round(dist, 1),
                "age_h": round(age_h, 1),
                "weight": round(weight, 2),
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
