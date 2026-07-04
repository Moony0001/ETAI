"""Sentinel-5P / TROPOMI adapter via Google Earth Engine — STUB.

TROPOMI column tracers (NO2, SO2, CO, UV Aerosol Index) are the satellite
"fingerprint" signals for the attribution engine: UVAI + CO strongly support
biomass smoke; SO2 supports industrial; NO2 supports traffic.

STATUS: interface wired, returns None gracefully when GEE is unauthenticated so
the pipeline still runs on ground + fire + met data alone.

TO ENABLE (one-time):
  1. pip install earthengine-api        (or: uv sync --extra gee)
  2. earthengine authenticate           (opens a browser; needs a GEE-enabled
                                         Google Cloud project)
  3. set the project id below or via EE_PROJECT env var.

GEE image collections to sample (reduceRegion over the ward polygon, recent pass):
  COPERNICUS/S5P/NRTI/L3_NO2   -> 'tropospheric_NO2_column_number_density'
  COPERNICUS/S5P/NRTI/L3_SO2   -> 'SO2_column_number_density'
  COPERNICUS/S5P/NRTI/L3_CO    -> 'CO_column_number_density'
  COPERNICUS/S5P/NRTI/L3_AER_AI-> 'absorbing_aerosol_index'   (this is UVAI)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.adapters.base import AbstractSourceAdapter
from backend.models import TropomiPoint

logger = logging.getLogger("vayulens.adapters.tropomi")

_EE_PROJECT = os.getenv("EE_PROJECT", "").strip()

_S5P_PRODUCTS = {
    "no2": ("COPERNICUS/S5P/NRTI/L3_NO2", "tropospheric_NO2_column_number_density"),
    "so2": ("COPERNICUS/S5P/NRTI/L3_SO2", "SO2_column_number_density"),
    "co": ("COPERNICUS/S5P/NRTI/L3_CO", "CO_column_number_density"),
    "uvai": ("COPERNICUS/S5P/NRTI/L3_AER_AI", "absorbing_aerosol_index"),
}


class TropomiAdapter(AbstractSourceAdapter):
    name = "tropomi"
    description = "Sentinel-5P column tracers via GEE (stub)"

    def __init__(self, cache_ttl_s: float = 6 * 3600.0) -> None:
        super().__init__(cache_ttl_s=cache_ttl_s)
        self._ee = None
        self._authed = False
        self._try_init()

    # ------------------------------------------------------------------
    def _try_init(self) -> None:
        """Attempt to import + initialise Earth Engine; stay disabled if it fails."""
        try:
            import ee  # type: ignore
        except ImportError:
            logger.info("[tropomi] earthengine-api not installed — stub returns None.")
            return
        try:
            if _EE_PROJECT:
                ee.Initialize(project=_EE_PROJECT)
            else:
                ee.Initialize()
            self._ee = ee
            self._authed = True
            logger.info("[tropomi] Earth Engine initialised.")
        except Exception as exc:  # noqa: BLE001 - EE raises many things
            logger.warning(
                "[tropomi] Earth Engine not authenticated (%s). "
                "Run `earthengine authenticate`. Returning None.",
                type(exc).__name__,
            )

    @property
    def available(self) -> bool:
        return self._authed

    # ------------------------------------------------------------------
    def sample(
        self,
        lat: float,
        lon: float,
        t: datetime,
        radius_m: float = 3500.0,
        lookback_hours: int = 24,
    ) -> Optional[TropomiPoint]:
        """Mean column tracers near (lat, lon) over the recent overpass window.

        Returns None (not zeros) when GEE is unavailable so downstream features
        can treat the satellite channel as simply "absent".
        """
        if not self.available or self._ee is None:
            return None
        ee = self._ee
        try:
            point = ee.Geometry.Point([lon, lat])
            region = point.buffer(radius_m)
            end = t.astimezone(timezone.utc)
            start = end - timedelta(hours=lookback_hours)
            values: dict[str, Optional[float]] = {}
            for key, (collection, band) in _S5P_PRODUCTS.items():
                img = (
                    ee.ImageCollection(collection)
                    .select(band)
                    .filterDate(start.isoformat(), end.isoformat())
                    .mean()
                )
                stat = img.reduceRegion(
                    reducer=ee.Reducer.mean(), geometry=region, scale=7000, maxPixels=1e9
                )
                values[key] = stat.get(band).getInfo() if stat else None
            return TropomiPoint(
                lat=lat,
                lon=lon,
                timestamp=t,
                no2=values.get("no2"),
                so2=values.get("so2"),
                co=values.get("co"),
                uvai=values.get("uvai"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[tropomi] sample failed (%s) — returning None.", exc)
            return None

    def fetch(self, **kwargs: Any) -> list[TropomiPoint]:
        lat, lon = kwargs.get("lat"), kwargs.get("lon")
        t = kwargs.get("t", datetime.now(timezone.utc))
        if lat is None or lon is None:
            return []
        pt = self.sample(lat, lon, t)
        return [pt] if pt else []
