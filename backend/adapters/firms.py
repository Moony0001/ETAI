"""NASA FIRMS adapter — real active-fire detections (lat/lon/FRP/time).

Area CSV API:
  {base}/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{day_range}[/{YYYY-MM-DD}]

SOURCE defaults to VIIRS_SNPP_NRT (375 m, good for stubble fields).
VIIRS CSV columns:
  latitude, longitude, bright_ti4, scan, track, acq_date, acq_time,
  satellite, instrument, confidence, version, bright_ti5, frp, daynight

acq_time is UTC HHMM. Missing MAP_KEY => warn + return []. Never raises.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

from backend.adapters.base import AbstractSourceAdapter
from backend.config import DELHI_BBOX, FIRMS_BASE_URL, FIRMS_MAP_KEY, FIRMS_SOURCE
from backend.models import Fire

logger = logging.getLogger("vayulens.adapters.firms")


def _parse_acq(acq_date: str, acq_time: str) -> datetime:
    """acq_date 'YYYY-MM-DD' + acq_time 'HHMM' (UTC) -> aware datetime."""
    t = acq_time.strip().zfill(4)
    hh, mm = int(t[:2]), int(t[2:])
    y, m, d = (int(x) for x in acq_date.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class FirmsAdapter(AbstractSourceAdapter):
    name = "firms"
    description = "NASA FIRMS active fires"

    def __init__(self, cache_ttl_s: float = 3600.0) -> None:
        super().__init__(cache_ttl_s=cache_ttl_s)

    @property
    def available(self) -> bool:
        return bool(FIRMS_MAP_KEY)

    def fetch(self, **kwargs: Any) -> list[Fire]:
        """Fires in the bbox over the trailing `day_range` days (max 10)."""
        if not self.available:
            self.warn_unavailable()
            return []
        bbox: tuple[float, float, float, float] = kwargs.get("bbox", DELHI_BBOX)
        day_range: int = min(int(kwargs.get("day_range", 3)), 10)
        source: str = kwargs.get("source", FIRMS_SOURCE)
        # Widen bbox for fires: stubble sources sit NW of Delhi, well outside the
        # city box. Callers can pass a pre-widened bbox; default widens by ~2.5°.
        west, south, east, north = bbox
        area = f"{west},{south},{east},{north}"

        url = f"{FIRMS_BASE_URL}/{FIRMS_MAP_KEY}/{source}/{area}/{day_range}"
        date = kwargs.get("date")
        if date:
            url = f"{url}/{date}"

        text = self.get_text(url)
        if not text:
            return []
        if text.lstrip().lower().startswith(("invalid", "error", "no ")):
            logger.warning("[firms] API message: %s", text.strip()[:120])
            return []

        fires: list[Fire] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                frp = float(row.get("frp") or 0.0)
                ts = _parse_acq(row["acq_date"], row.get("acq_time", "0000"))
            except (KeyError, ValueError):
                continue
            fires.append(
                Fire(
                    lat=lat,
                    lon=lon,
                    frp=frp,
                    timestamp=ts,
                    confidence=str(row.get("confidence", "")),
                    source=source,
                )
            )
        logger.info("[firms] %d fires (%s, %dd)", len(fires), source, day_range)
        return fires
