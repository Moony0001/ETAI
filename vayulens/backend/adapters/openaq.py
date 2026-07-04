"""OpenAQ v3 adapter — real ground PM2.5/PM10/NO2/SO2/CO/O3 by station.

Auth: X-API-Key header (free key from https://explore.openaq.org/register).
Flow:
  1. /v3/locations?bbox=W,S,E,N   -> stations + their sensors (param -> sensor id)
  2. /v3/locations/{id}/latest    -> current value per sensor (mapped back to param)
  3. /v3/sensors/{id}/hours       -> hourly history (used for the clean-day baseline)

Missing key => logs a warning and returns []. Never raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.adapters.base import AbstractSourceAdapter
from backend.config import (
    DELHI_BBOX,
    OPENAQ_API_KEY,
    OPENAQ_BASE_URL,
    OPENAQ_PARAM_MAP,
)
from backend.models import Reading, Station

logger = logging.getLogger("vayulens.adapters.openaq")


def _iso(dt: str | None) -> Optional[datetime]:
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except ValueError:
        return None


class OpenAQAdapter(AbstractSourceAdapter):
    name = "openaq"
    description = "OpenAQ v3 ground sensors"

    def __init__(self, cache_ttl_s: float = 1800.0) -> None:
        super().__init__(cache_ttl_s=cache_ttl_s)
        self._headers = {"X-API-Key": OPENAQ_API_KEY} if OPENAQ_API_KEY else {}
        # sensor_id -> (station_id, parameter) index, filled by list_stations()
        self._sensor_index: dict[int, tuple[str, str]] = {}

    @property
    def available(self) -> bool:
        return bool(OPENAQ_API_KEY)

    # ------------------------------------------------------------------
    def list_stations(
        self, bbox: tuple[float, float, float, float] = DELHI_BBOX, limit: int = 1000
    ) -> list[Station]:
        """Stations (OpenAQ locations) within the bbox, with param->sensor map."""
        if not self.available:
            self.warn_unavailable()
            return []
        west, south, east, north = bbox
        data = self.get_json(
            f"{OPENAQ_BASE_URL}/locations",
            params={"bbox": f"{west},{south},{east},{north}", "limit": limit},
            headers=self._headers,
        )
        if not data:
            return []

        stations: list[Station] = []
        for loc in data.get("results", []):
            coords = loc.get("coordinates") or {}
            lat, lon = coords.get("latitude"), coords.get("longitude")
            if lat is None or lon is None:
                continue
            sensors: dict[str, int] = {}
            for sensor in loc.get("sensors", []) or []:
                param = ((sensor.get("parameter") or {}).get("name") or "").lower()
                short = OPENAQ_PARAM_MAP.get(param)
                sid = sensor.get("id")
                if short and sid is not None:
                    sensors[short] = sid
                    self._sensor_index[sid] = (str(loc.get("id")), short)
            provider = (loc.get("provider") or {}).get("name")
            stations.append(
                Station(
                    station_id=str(loc.get("id")),
                    name=loc.get("name") or f"loc-{loc.get('id')}",
                    lat=float(lat),
                    lon=float(lon),
                    provider=provider,
                    sensors=sensors,
                )
            )
        logger.info("[openaq] %d stations in bbox", len(stations))
        return stations

    # ------------------------------------------------------------------
    def latest_for_station(self, station: Station) -> list[Reading]:
        """Latest value per sensor at one station."""
        if not self.available:
            return []
        data = self.get_json(
            f"{OPENAQ_BASE_URL}/locations/{station.station_id}/latest",
            headers=self._headers,
        )
        if not data:
            return []
        readings: list[Reading] = []
        # sensor_id -> param for THIS station (built during list_stations, but
        # re-derive locally in case list_stations wasn't called first)
        local = {sid: p for p, sid in station.sensors.items()}
        for row in data.get("results", []):
            sid = row.get("sensorsId")
            param = local.get(sid) or (self._sensor_index.get(sid, (None, None))[1])
            value = row.get("value")
            if param is None or value is None:
                continue
            ts = _iso(((row.get("datetime") or {}).get("utc"))) or datetime.now(timezone.utc)
            coords = row.get("coordinates") or {}
            readings.append(
                Reading(
                    station_id=station.station_id,
                    parameter=param,  # type: ignore[arg-type]
                    value=float(value),
                    unit="µg/m³" if param != "co" else "mg/m³",
                    timestamp=ts,
                    lat=float(coords.get("latitude", station.lat)),
                    lon=float(coords.get("longitude", station.lon)),
                )
            )
        return readings

    # ------------------------------------------------------------------
    def history(
        self,
        sensor_id: int,
        parameter: str,
        station: Station,
        datetime_from: datetime,
        datetime_to: datetime,
        limit: int = 1000,
    ) -> list[Reading]:
        """Hourly aggregated history for one sensor (for the baseline)."""
        if not self.available:
            return []
        data = self.get_json(
            f"{OPENAQ_BASE_URL}/sensors/{sensor_id}/hours",
            params={
                "datetime_from": datetime_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "datetime_to": datetime_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": limit,
            },
            headers=self._headers,
        )
        if not data:
            return []
        readings: list[Reading] = []
        for row in data.get("results", []):
            value = row.get("value")
            if value is None:
                continue
            period = row.get("period") or {}
            ts = _iso((period.get("datetimeFrom") or {}).get("utc")) or datetime.now(timezone.utc)
            readings.append(
                Reading(
                    station_id=station.station_id,
                    parameter=parameter,  # type: ignore[arg-type]
                    value=float(value),
                    unit="µg/m³",
                    timestamp=ts,
                    lat=station.lat,
                    lon=station.lon,
                )
            )
        return readings

    # ------------------------------------------------------------------
    def fetch(self, **kwargs: Any) -> list[Reading]:
        """Latest readings across all stations in the bbox."""
        bbox = kwargs.get("bbox", DELHI_BBOX)
        stations = self.list_stations(bbox=bbox)
        readings: list[Reading] = []
        for st in stations:
            readings.extend(self.latest_for_station(st))
        logger.info("[openaq] %d latest readings", len(readings))
        return readings
