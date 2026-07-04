"""Open-Meteo adapter — real, keyless hourly meteorology.

Provides wind (m/s + u/v components), boundary-layer height, RH, temperature,
precipitation. Used both for ward-level features and for sampling the wind
field along back-trajectories.

Forecast endpoint (with past_days) covers the recent window; the archive
endpoint is available for older history. No API key required.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

from backend.adapters.base import AbstractSourceAdapter
from backend.config import (
    OPENMETEO_ARCHIVE_URL,
    OPENMETEO_FORECAST_URL,
)
from backend.models import MetPoint

logger = logging.getLogger("vayulens.adapters.openmeteo")

_HOURLY_VARS = [
    "wind_speed_10m",
    "wind_direction_10m",
    "boundary_layer_height",
    "relative_humidity_2m",
    "temperature_2m",
    "precipitation",
]


def wind_components(speed_ms: float, direction_deg: float) -> tuple[float, float]:
    """Meteorological (from-)direction -> (u eastward, v northward) m/s.

    u = -speed * sin(dir);  v = -speed * cos(dir)
    e.g. wind FROM the west (270°) => u=+speed (blows east), v=0.
    """
    rad = math.radians(direction_deg)
    u = -speed_ms * math.sin(rad)
    v = -speed_ms * math.cos(rad)
    return u, v


def _grid_round(x: float, step: float = 0.25) -> float:
    """Snap coords to a coarse grid so the wind-field cache is reusable."""
    return round(x / step) * step


class OpenMeteoAdapter(AbstractSourceAdapter):
    name = "openmeteo"
    description = "Open-Meteo hourly weather"

    def __init__(self, cache_ttl_s: float = 3600.0) -> None:
        super().__init__(cache_ttl_s=cache_ttl_s)

    @property
    def available(self) -> bool:
        return True  # keyless

    # ------------------------------------------------------------------
    def series(
        self,
        lat: float,
        lon: float,
        *,
        past_days: int = 3,
        forecast_days: int = 1,
        archive: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[MetPoint]:
        """Hourly MetPoint series at (lat, lon), snapped to a 0.25° grid cell."""
        glat, glon = _grid_round(lat), _grid_round(lon)
        params: dict[str, Any] = {
            "latitude": glat,
            "longitude": glon,
            "hourly": ",".join(_HOURLY_VARS),
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        if archive and start_date and end_date:
            url = OPENMETEO_ARCHIVE_URL
            params["start_date"] = start_date
            params["end_date"] = end_date
        else:
            url = OPENMETEO_FORECAST_URL
            params["past_days"] = past_days
            params["forecast_days"] = forecast_days

        data = self.get_json(url, params=params)
        if not data:
            return []
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        out: list[MetPoint] = []
        for i, tstr in enumerate(times):
            try:
                ts = datetime.fromisoformat(tstr).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            speed = _at(hourly.get("wind_speed_10m"), i)
            wdir = _at(hourly.get("wind_direction_10m"), i)
            if speed is None or wdir is None:
                continue
            u, v = wind_components(speed, wdir)
            out.append(
                MetPoint(
                    lat=glat,
                    lon=glon,
                    timestamp=ts,
                    wind_speed=speed,
                    wind_dir=wdir,
                    u=u,
                    v=v,
                    blh=_at(hourly.get("boundary_layer_height"), i),
                    rh=_at(hourly.get("relative_humidity_2m"), i),
                    temp=_at(hourly.get("temperature_2m"), i),
                    precip=_at(hourly.get("precipitation"), i),
                )
            )
        return out

    # ------------------------------------------------------------------
    def wind_at(self, lat: float, lon: float, t: datetime) -> Optional[MetPoint]:
        """Nearest-in-time MetPoint at a point — used by the trajectory sampler."""
        series = self.series(lat, lon, past_days=3, forecast_days=1)
        return nearest_in_time(series, t)

    def fetch(self, **kwargs: Any) -> list[MetPoint]:
        lat = kwargs.get("lat")
        lon = kwargs.get("lon")
        if lat is None or lon is None:
            return []
        return self.series(
            lat,
            lon,
            past_days=kwargs.get("past_days", 3),
            forecast_days=kwargs.get("forecast_days", 1),
        )


def _at(arr: Optional[list], i: int) -> Optional[float]:
    if not arr or i >= len(arr):
        return None
    v = arr[i]
    return float(v) if v is not None else None


def nearest_in_time(series: list[MetPoint], t: datetime) -> Optional[MetPoint]:
    if not series:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return min(series, key=lambda m: abs((m.timestamp - t).total_seconds()))
