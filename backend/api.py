"""FastAPI surface for vayulens.

Endpoints:
  GET /health                    -> service + adapter availability
  GET /wards                     -> list wards (real polygons or grid fallback)
  GET /attribution/{ward_id}     -> full WardAttribution (runs the pipeline)
  GET /attribution               -> default ward (central Delhi)
  GET /trajectory/{ward_id}      -> back-trajectory + contributing fires as GeoJSON

Run: uvicorn backend.api:app --reload
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.adapters.firms import FirmsAdapter
from backend.adapters.geodata import GeoDataAdapter
from backend.adapters.openaq import OpenAQAdapter
from backend.adapters.tropomi import TropomiAdapter
from backend.config import DELHI_BBOX, DELHI_CENTER
from backend.models import WardAttribution, WardSummary
from backend.pipeline import run_attribution

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="vayulens",
    version="0.1.0",
    description="AI-powered urban air-quality source attribution (Delhi vertical slice).",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_geo = GeoDataAdapter()


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


@app.get("/attribution", response_model=WardAttribution)
def attribution_default() -> WardAttribution:
    ward = _geo.ward_at(*DELHI_CENTER)
    ward_id = ward.ward_id if ward else None
    return run_attribution(ward_id=ward_id).attribution


@app.get("/attribution/{ward_id}", response_model=WardAttribution)
def attribution(ward_id: str) -> WardAttribution:
    if _geo.get_ward(ward_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ward_id '{ward_id}'")
    return run_attribution(ward_id=ward_id).attribution


@app.get("/trajectory/{ward_id}")
def trajectory(ward_id: str) -> dict:
    if _geo.get_ward(ward_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ward_id '{ward_id}'")
    result = run_attribution(ward_id=ward_id)
    return {
        "ward_id": ward_id,
        "hours": len(result.path) - 1 if result.path else 0,
        "n_contributing_fires": len(result.contributors),
        "geojson": result.trajectory_geojson,
    }


@app.get("/trajectory")
def trajectory_default() -> dict:
    ward = _geo.ward_at(*DELHI_CENTER)
    return trajectory(ward.ward_id if ward else _geo.load_wards()[0].ward_id)
