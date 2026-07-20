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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.adapters.base import AbstractSourceAdapter
from backend.config import (
    DELHI_BBOX,
    FIRMS_ARCHIVE_SOURCE,
    FIRMS_AVAILABILITY_URL,
    FIRMS_BASE_URL,
    FIRMS_MAP_KEY,
    FIRMS_SOURCE,
)
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

    # ------------------------------------------------------------------
    # Archive-aware fetching (historical episode dates)
    # ------------------------------------------------------------------
    def _availability(self) -> dict[str, tuple[datetime, datetime]]:
        """Per-source [min_date, max_date] from the FIRMS data-availability API.

        NRT sources cover only the last ~2 months; the standard-processing (SP)
        archive covers 2012-present but lags ~2 months. This tells us which source
        actually holds a given date instead of guessing.
        """
        if not self.available:
            return {}
        text = self.get_text(f"{FIRMS_AVAILABILITY_URL}/{FIRMS_MAP_KEY}/all")
        if not text:
            return {}
        out: dict[str, tuple[datetime, datetime]] = {}
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 3:
                continue
            try:
                lo = datetime.strptime(row[1].strip(), "%Y-%m-%d")
                hi = datetime.strptime(row[2].strip(), "%Y-%m-%d")
            except (ValueError, AttributeError):
                continue  # header / min_date=='N/A' rows
            out[row[0].strip()] = (lo, hi)
        return out

    def source_for_date(self, t: datetime) -> tuple[Optional[str], str]:
        """Pick (source, provenance) for date t: NRT→'live', SP archive→'archive'."""
        d = datetime(t.year, t.month, t.day)
        avail = self._availability()
        for source, provenance in ((FIRMS_SOURCE, "live"), (FIRMS_ARCHIVE_SOURCE, "archive")):
            rng = avail.get(source)
            if rng and rng[0] <= d <= rng[1]:
                return source, provenance
        # Availability lookup failed (offline) — fall back to an age heuristic.
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - d).days
        return (FIRMS_SOURCE, "live") if age <= 60 else (FIRMS_ARCHIVE_SOURCE, "archive")

    def fetch_for_date(
        self, bbox: tuple[float, float, float, float], t: datetime, day_range: int = 2
    ) -> tuple[list[Fire], str]:
        """Fires near date t from the right source. Returns (fires, provenance).

        Fetches a window covering the 24 h before t (the trajectory look-back)
        through the day of t. provenance is 'live' | 'archive' | 'none'.
        """
        if not self.available:
            self.warn_unavailable()
            return [], "none"
        source, provenance = self.source_for_date(t)
        if source is None:
            return [], "none"
        start = (t - timedelta(days=1)).strftime("%Y-%m-%d")
        fires = self.fetch(bbox=bbox, day_range=day_range, source=source, date=start)
        return fires, (provenance if fires else "none")
