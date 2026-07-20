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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.adapters.base import AbstractSourceAdapter
from backend.config import (
    OPENMETEO_ARCHIVE_URL,
    OPENMETEO_FORECAST_URL,
    OPENMETEO_HISTORICAL_FORECAST_URL,
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
    def series_window(self, lat: float, lon: float, t: datetime) -> list[MetPoint]:
        """Pick forecast (recent) vs archive (historical) automatically for time t.

        Recent window -> /forecast with past_days; older than ~5 days -> the ERA5
        /archive endpoint. This is what lets `--date <past stubble episode>` pull
        REAL historical wind for the Delhi->Punjab corridor demo.
        """
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - t).days
        if age_days <= 5:
            return self.series(lat, lon, past_days=max(3, age_days + 2), forecast_days=1)
        start = (t - timedelta(days=2)).strftime("%Y-%m-%d")
        end = (t + timedelta(days=1)).strftime("%Y-%m-%d")
        return self.series(lat, lon, archive=True, start_date=start, end_date=end)

    # ------------------------------------------------------------------
    def level_series_window(
        self, lat: float, lon: float, t: datetime, level: str
    ) -> list[MetPoint]:
        """Hourly wind at a pressure level (e.g. '850hPa') around time t.

        ERA5 archive only exposes surface winds, so pressure-level winds for
        historical dates come from the Historical Forecast API (archived forecast
        runs, 2022-present); recent dates use the forecast endpoint. If the level
        is unavailable (older than the archive, or a gap), we fall back to 10 m so
        the trajectory always advects on *some* real wind.
        """
        if not level or level == "10m":
            return self.series_window(lat, lon, t)

        glat, glon = _grid_round(lat), _grid_round(lon)
        spd_var, dir_var = f"wind_speed_{level}", f"wind_direction_{level}"
        params: dict[str, Any] = {
            "latitude": glat,
            "longitude": glon,
            "hourly": f"{spd_var},{dir_var}",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        age_days = (datetime.now(timezone.utc) - t).days
        if age_days <= 2:
            url = OPENMETEO_FORECAST_URL
            params["past_days"] = min(92, max(3, age_days + 2))
            params["forecast_days"] = 1
        else:
            url = OPENMETEO_HISTORICAL_FORECAST_URL
            params["start_date"] = (t - timedelta(days=2)).strftime("%Y-%m-%d")
            params["end_date"] = (t + timedelta(days=1)).strftime("%Y-%m-%d")

        data = self.get_json(url, params=params)
        pts = self._parse_level(data, glat, glon, spd_var, dir_var, level) if data else []
        if not pts:
            logger.warning(
                "[openmeteo] %s wind unavailable at %.2f,%.2f (t=%s) — falling back to 10m.",
                level, glat, glon, t.date(),
            )
            return self.series_window(lat, lon, t)
        return pts

    @staticmethod
    def _parse_level(
        data: dict, glat: float, glon: float, spd_var: str, dir_var: str, level: str
    ) -> list[MetPoint]:
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        out: list[MetPoint] = []
        for i, tstr in enumerate(times):
            try:
                ts = datetime.fromisoformat(tstr).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            speed = _at(hourly.get(spd_var), i)
            wdir = _at(hourly.get(dir_var), i)
            if speed is None or wdir is None:
                continue
            u, v = wind_components(speed, wdir)
            out.append(
                MetPoint(
                    lat=glat, lon=glon, timestamp=ts,
                    wind_speed=speed, wind_dir=wdir, u=u, v=v, level=level,
                )
            )
        return out

    def make_wind_fn(self, t: datetime, level: Optional[str] = None):
        """Return wind_fn(lat, lon, tt) for the trajectory, caching per grid cell.

        `level` picks the wind level (default 10 m surface). A pressure level like
        '850hPa' advects the parcel through the boundary layer for realistic
        long-range smoke transport; it falls back to 10 m where unavailable.
        """
        cache: dict[tuple[float, float], list[MetPoint]] = {}

        def wind_fn(lat: float, lon: float, tt: datetime) -> Optional[MetPoint]:
            key = (_grid_round(lat), _grid_round(lon))
            if key not in cache:
                cache[key] = self.level_series_window(lat, lon, t, level or "10m")
            return nearest_in_time(cache[key], tt)

        return wind_fn

    def wind_at(self, lat: float, lon: float, t: datetime) -> Optional[MetPoint]:
        """Nearest-in-time MetPoint at a point — used by the trajectory sampler."""
        return nearest_in_time(self.series_window(lat, lon, t), t)

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
