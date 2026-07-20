"""FastAPI surface for vayulens.

Endpoints:
  GET /health                    -> service + adapter availability
  GET /wards                     -> list wards (real polygons or grid fallback)
  GET /attribution               -> WHOLE-CITY batch: { meta, geojson } (map choropleth)
  GET /attribution/{ward_id}     -> full WardAttribution for one ward (runs the pipeline)
  GET /trajectory                -> back-trajectory for a point / ward centroid + date
  GET /trajectory/{ward_id}      -> back-trajectory + contributing fires as GeoJSON

Run: uvicorn backend.api:app --reload
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.adapters.firms import FirmsAdapter
from backend.adapters.geodata import GeoDataAdapter
from backend.adapters.openaq import OpenAQAdapter
from backend.adapters.tropomi import TropomiAdapter
from backend.config import (
    DELHI_BBOX,
    DELHI_CENTER,
    TRAJECTORY_LEVELS_AVAILABLE,
    TRAJECTORY_PRESSURE_LEVEL,
)
from backend.models import WardAttribution, WardSummary
from backend.pipeline import run_attribution, run_attribution_batch, run_trajectory
from backend.store import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vayulens.api")

app = FastAPI(
    title="vayulens",
    version="0.2.0",
    description="AI-powered urban air-quality source attribution (Delhi vertical slice).",
)
# Allow the Vite dev server (and anything else in dev) to call the API directly.
# The frontend also has a /api -> :8000 Vite proxy, so either path works CORS-free.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "*",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

_geo = GeoDataAdapter()


def _parse_date(date: Optional[str]) -> Optional[datetime]:
    """Parse a YYYY-MM-DD query param to a UTC datetime (noon = smog-representative).

    Returns None for a missing/blank date so the pipeline uses "now" (latest).
    """
    if not date or not date.strip():
        return None
    try:
        d = datetime.strptime(date.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad date '{date}'; expected YYYY-MM-DD")
    return d.replace(hour=12, tzinfo=timezone.utc)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "city": "Delhi",
        "bbox": DELHI_BBOX,
        "adapters": {
            "openaq": OpenAQAdapter().available,
            "firms": FirmsAdapter().available,
            "openmeteo": True,
            "tropomi": TropomiAdapter().available,
        },
    }


@app.get("/wards", response_model=list[WardSummary])
def wards(limit: int = Query(500, ge=1, le=5000)) -> list[WardSummary]:
    return [w.summary() for w in _geo.load_wards()[:limit]]


@app.get("/attribution")
def attribution_batch(
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to latest/today"),
    refresh: bool = Query(False, description="Recompute even if a cached batch exists"),
) -> dict:
    """Whole-city attribution as { meta, geojson }.

    geojson is a FeatureCollection of ward (or grid) polygons; each feature's
    properties carry pm25/aqi/aqi_band/excess/shares/masses/confidence/
    top_driver_text. Results are cached in DuckDB keyed by date so repeat demo
    runs are instant and don't hammer the upstream APIs.
    """
    t = _parse_date(date)
    date_str = (t or datetime.now(timezone.utc)).strftime("%Y-%m-%d")

    con = None
    try:
        con = db.connect()
        if not refresh:
            cached = db.load_attribution_batch(con, date_str)
            if cached is not None:
                meta, geojson = cached
                meta = {**meta, "cached": True}
                return {"meta": meta, "geojson": geojson}
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        logger.warning("[api] batch cache read skipped: %s", exc)

    result = run_attribution_batch(t=t)
    try:
        if con is None:
            con = db.connect()
        db.save_attribution_batch(con, date_str, result.meta, result.geojson)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[api] batch cache write skipped: %s", exc)
    finally:
        if con is not None:
            con.close()

    return {"meta": {**result.meta, "cached": False}, "geojson": result.geojson}


@app.get("/attribution/{ward_id}", response_model=WardAttribution)
def attribution(
    ward_id: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to latest/today"),
) -> WardAttribution:
    if _geo.get_ward(ward_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ward_id '{ward_id}'")
    return run_attribution(ward_id=ward_id, t=_parse_date(date)).attribution


def _resolve_level(level: Optional[str]) -> str:
    if not level:
        return TRAJECTORY_PRESSURE_LEVEL
    if level not in TRAJECTORY_LEVELS_AVAILABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Bad level '{level}'; expected one of {list(TRAJECTORY_LEVELS_AVAILABLE)}",
        )
    return level


@app.get("/trajectory/{ward_id}")
def trajectory(
    ward_id: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to latest/today"),
    level: Optional[str] = Query(None, description="Wind level, e.g. 850hPa or 10m"),
) -> dict:
    ward = _geo.get_ward(ward_id)
    if ward is None:
        raise HTTPException(status_code=404, detail=f"Unknown ward_id '{ward_id}'")
    return run_trajectory(ward, _parse_date(date), _resolve_level(level))


@app.get("/trajectory")
def trajectory_default(
    ward_id: Optional[str] = Query(None, description="Ward id to trace from"),
    lat: Optional[float] = Query(None, description="Point latitude (with lon)"),
    lon: Optional[float] = Query(None, description="Point longitude (with lat)"),
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to latest/today"),
    level: Optional[str] = Query(None, description="Wind level, e.g. 850hPa (default) or 10m"),
) -> dict:
    """Back-trajectory for an explicit ward, a lat/lon point, or the city centre.

    `level` picks the advection wind level so a judge can compare 10 m vs 850 hPa
    live. Uses the fast trajectory-only path (no attribution) — cached per
    (ward, date, level).
    """
    t = _parse_date(date)
    lvl = _resolve_level(level)
    if ward_id:
        ward = _geo.get_ward(ward_id)
        if ward is None:
            raise HTTPException(status_code=404, detail=f"Unknown ward_id '{ward_id}'")
        return run_trajectory(ward, t, lvl)
    if lat is not None and lon is not None:
        ward = _geo.ward_at(lat, lon)
        if ward is None:
            raise HTTPException(status_code=404, detail="No ward near that point")
        return run_trajectory(ward, t, lvl)
    ward = _geo.ward_at(*DELHI_CENTER) or _geo.load_wards()[0]
    return run_trajectory(ward, t, lvl)
