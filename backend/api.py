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
    ENFORCEMENT_LOCAL_SOURCES,
    TRAJECTORY_LEVELS_AVAILABLE,
    TRAJECTORY_PRESSURE_LEVEL,
)
from backend import narration
from backend.enforcement import rank_enforcement
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


def _get_or_compute_batch(
    t: Optional[datetime], date_str: str, refresh: bool = False
) -> tuple[dict, dict, bool]:
    """Return (meta, geojson, cached) for a date — from the DuckDB cache if present
    (and not refreshing), else compute the whole-city batch once and cache it.

    Shared by /attribution and /enforcement so the enforcement queue never
    triggers a second 2,700-cell sweep.
    """
    con = None
    try:
        con = db.connect()
        if not refresh:
            cached = db.load_attribution_batch(con, date_str)
            if cached is not None:
                meta, geojson = cached
                return meta, geojson, True
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
    return result.meta, result.geojson, False


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
    meta, geojson, cached = _get_or_compute_batch(t, date_str, refresh)
    return {"meta": {**meta, "cached": cached}, "geojson": geojson}


@app.get("/enforcement")
def enforcement(
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to latest/today"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Wards ranked by locally-actionable pollution (not raw AQI).

    Returns a prioritised `queue` (traffic/dust/industrial mass, confidence-
    weighted, with a recommended action) plus a short `regional` contrast list of
    biomass/regional-dominated wards that need coordination, not local enforcement.
    Built from the same cached batch as /attribution.
    """
    t = _parse_date(date)
    date_str = (t or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    meta, geojson, cached = _get_or_compute_batch(t, date_str)
    queue, regional = rank_enforcement(geojson["features"], limit=limit)
    return {
        "meta": {
            "city": meta.get("city", "Delhi"),
            "date": meta.get("date", date_str),
            "cached": cached,
            "queued": len(queue),
            "local_sources": list(ENFORCEMENT_LOCAL_SOURCES),
        },
        "queue": queue,
        "regional": regional,
    }


@app.get("/narration/{ward_id}")
def narration_ward(
    ward_id: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to latest/today"),
) -> dict:
    """Plain-language explanation of a ward's attribution (+ EN/HI health advisory).

    Explains numbers the engine already produced; the LLM never computes them.
    Cached in DuckDB per (ward, date). Falls back to deterministic text when the
    Anthropic key is absent — never empty, never a stall.
    """
    t = _parse_date(date)
    date_str = (t or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    con = None
    try:
        con = db.connect()
        cached = db.load_narration(con, "ward", ward_id, date_str)
        if cached is not None:
            return {**cached, "cached": True}
        _meta, geojson, _c = _get_or_compute_batch(t, date_str)
        feat = next(
            (f for f in geojson["features"] if f["properties"].get("ward_id") == ward_id), None
        )
        if feat is None:
            raise HTTPException(status_code=404, detail=f"Unknown ward_id '{ward_id}'")
        traj = db.load_trajectory(con, ward_id, date_str, TRAJECTORY_PRESSURE_LEVEL)
        result = narration.ward_narration(feat["properties"], trajectory=traj)
        db.save_narration(con, "ward", ward_id, date_str, result)
        return {**result, "cached": False}
    finally:
        if con is not None:
            con.close()


@app.get("/enforcement/narration")
def enforcement_narration(
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to latest/today"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """One-line rationale per queued ward. Cached per (date, limit); graceful fallback."""
    t = _parse_date(date)
    date_str = (t or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    key = f"limit{limit}"
    con = None
    try:
        con = db.connect()
        cached = db.load_narration(con, "enforcement", key, date_str)
        if cached is not None:
            return {**cached, "cached": True}
        _meta, geojson, _c = _get_or_compute_batch(t, date_str)
        queue, _regional = rank_enforcement(geojson["features"], limit=limit)
        result = {"date": date_str, "rationales": narration.enforcement_rationales(queue)}
        db.save_narration(con, "enforcement", key, date_str, result)
        return {**result, "cached": False}
    finally:
        if con is not None:
            con.close()


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
