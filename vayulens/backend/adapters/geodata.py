"""Geodata adapter — ward polygons + OSM road/industrial/construction proximity.

Ward source of truth:
  * data/geo/delhi_wards.geojson if present (see README for where to get it), else
  * a ~1 km grid over DELHI_BBOX so the pipeline always has wards to attribute.

Proximity features (road / construction / industrial "stack") come from the
OpenStreetMap Overpass API, computed lazily per ward and cached. If Overpass is
unreachable we fall back to a transparent distance-to-centre heuristic so the
pipeline never blocks — the fallback is flagged in the returned dict.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry

from backend.config import (
    CONSTRUCTION_BUFFER_M,
    DELHI_BBOX,
    DELHI_CENTER,
    EARTH_RADIUS_M,
    GRID_CELL_KM,
    HTTP_TIMEOUT_S,
    INDUSTRIAL_BUFFER_M,
    OVERPASS_URL,
    RAW_DIR,
    ROAD_BUFFER_M,
    WARD_GEOJSON,
)
from backend.models import WardSummary

logger = logging.getLogger("vayulens.adapters.geodata")

# candidate property keys in real Delhi ward GeoJSONs
_ID_KEYS = ("ward_id", "Ward_No", "wardno", "WARD_NO", "wardcode", "Ward_Code", "id")
_NAME_KEYS = ("ward_name", "Ward_Name", "wardname", "WARD_NAME", "name", "Name")


@dataclass
class Ward:
    ward_id: str
    ward_name: str
    lat: float
    lon: float
    geometry: BaseGeometry
    is_grid: bool = False
    _proximity: Optional[dict[str, Any]] = field(default=None, repr=False)

    def summary(self) -> WardSummary:
        return WardSummary(
            ward_id=self.ward_id, ward_name=self.ward_name, lat=self.lat, lon=self.lon
        )


def _first(props: dict, keys: tuple[str, ...], default: str) -> str:
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return str(props[k])
    return default


def _saturate(x: float, half: float) -> float:
    """Map [0, inf) -> [0, 1) with a soft knee at `half`."""
    if x <= 0:
        return 0.0
    return x / (x + half)


class GeoDataAdapter:
    name = "geodata"

    def __init__(self) -> None:
        self._wards: Optional[list[Ward]] = None
        self._cache_dir = RAW_DIR / "overpass"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Ward loading
    # ------------------------------------------------------------------
    def load_wards(self) -> list[Ward]:
        if self._wards is not None:
            return self._wards
        if WARD_GEOJSON.exists():
            try:
                self._wards = self._load_geojson(WARD_GEOJSON)
                logger.info("[geodata] loaded %d wards from %s", len(self._wards), WARD_GEOJSON.name)
                return self._wards
            except Exception as exc:  # noqa: BLE001
                logger.warning("[geodata] failed to read ward file (%s); using grid.", exc)
        self._wards = self._grid_wards()
        logger.info("[geodata] no ward file — generated %d ~%.0fkm grid cells",
                    len(self._wards), GRID_CELL_KM)
        return self._wards

    def _load_geojson(self, path) -> list[Ward]:
        # Prefer geopandas if available; fall back to plain json + shapely.
        try:
            import geopandas as gpd  # type: ignore

            gdf = gpd.read_file(path)
            wards: list[Ward] = []
            for i, row in gdf.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                props = {k: row[k] for k in gdf.columns if k != "geometry"}
                c = geom.centroid
                wards.append(
                    Ward(
                        ward_id=_first(props, _ID_KEYS, f"ward_{i}"),
                        ward_name=_first(props, _NAME_KEYS, f"Ward {i}"),
                        lat=float(c.y),
                        lon=float(c.x),
                        geometry=geom,
                    )
                )
            return wards
        except ImportError:
            data = json.loads(path.read_text())
            wards = []
            for i, feat in enumerate(data.get("features", [])):
                geom = shape(feat["geometry"])
                if geom.is_empty:
                    continue
                props = feat.get("properties", {}) or {}
                c = geom.centroid
                wards.append(
                    Ward(
                        ward_id=_first(props, _ID_KEYS, f"ward_{i}"),
                        ward_name=_first(props, _NAME_KEYS, f"Ward {i}"),
                        lat=float(c.y),
                        lon=float(c.x),
                        geometry=geom,
                    )
                )
            return wards

    def _grid_wards(self) -> list[Ward]:
        west, south, east, north = DELHI_BBOX
        # ~km per degree at this latitude
        km_per_deg_lat = 111.0
        km_per_deg_lon = 111.0 * math.cos(math.radians((south + north) / 2))
        dlat = GRID_CELL_KM / km_per_deg_lat
        dlon = GRID_CELL_KM / km_per_deg_lon
        wards: list[Ward] = []
        r = 0
        y = south
        while y < north:
            c = 0
            x = west
            while x < east:
                cell = box(x, y, min(x + dlon, east), min(y + dlat, north))
                cx, cy = cell.centroid.x, cell.centroid.y
                wards.append(
                    Ward(
                        ward_id=f"grid_r{r:02d}_c{c:02d}",
                        ward_name=f"Grid ({r:02d},{c:02d})",
                        lat=cy,
                        lon=cx,
                        geometry=cell,
                        is_grid=True,
                    )
                )
                c += 1
                x += dlon
            r += 1
            y += dlat
        return wards

    # ------------------------------------------------------------------
    def get_ward(self, ward_id: str) -> Optional[Ward]:
        for w in self.load_wards():
            if w.ward_id == ward_id:
                return w
        return None

    def ward_at(self, lat: float, lon: float) -> Optional[Ward]:
        pt = Point(lon, lat)
        for w in self.load_wards():
            if w.geometry.contains(pt):
                return w
        # nearest centroid fallback
        wards = self.load_wards()
        if not wards:
            return None
        return min(wards, key=lambda w: (w.lat - lat) ** 2 + (w.lon - lon) ** 2)

    # ------------------------------------------------------------------
    # Proximity features via Overpass (cached, with heuristic fallback)
    # ------------------------------------------------------------------
    def proximity(self, ward: Ward) -> dict[str, Any]:
        """Return {road_proximity, construction_proximity, stack_proximity, source}."""
        if ward._proximity is not None:
            return ward._proximity
        cached = self._read_prox_cache(ward.ward_id)
        if cached is not None:
            ward._proximity = cached
            return cached

        result = self._overpass_proximity(ward)
        if result is None:
            result = self._heuristic_proximity(ward)
        ward._proximity = result
        self._write_prox_cache(ward.ward_id, result)
        return result

    def _overpass_proximity(self, ward: Ward) -> Optional[dict[str, Any]]:
        minx, miny, maxx, maxy = ward.geometry.bounds
        south, west, north, east = miny, minx, maxy, maxx
        bbox = f"{south},{west},{north},{east}"
        query = f"""
        [out:json][timeout:20];
        (
          way["highway"~"motorway|trunk|primary|secondary|tertiary"]({bbox});
          way["landuse"="industrial"]({bbox});
          way["man_made"="works"]({bbox});
          way["landuse"="construction"]({bbox});
          way["building"="construction"]({bbox});
        );
        out tags center;
        """
        try:
            resp = httpx.post(OVERPASS_URL, data={"data": query}, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("[geodata] Overpass failed (%s) — heuristic fallback.", exc)
            return None

        roads = industrial = construction = 0
        for el in elements:
            tags = el.get("tags", {})
            if "highway" in tags:
                roads += 1
            if tags.get("landuse") == "industrial" or tags.get("man_made") == "works":
                industrial += 1
            if tags.get("landuse") == "construction" or tags.get("building") == "construction":
                construction += 1

        # normalise counts to [0,1] with soft knees (tunable via magnitude)
        return {
            "road_proximity": round(_saturate(roads, half=25), 3),
            "construction_proximity": round(_saturate(construction, half=5), 3),
            "stack_proximity": round(_saturate(industrial, half=3), 3),
            "raw": {"roads": roads, "industrial": industrial, "construction": construction},
            "source": "overpass",
        }

    def _heuristic_proximity(self, ward: Ward) -> dict[str, Any]:
        """Distance-to-centre heuristic; clearly flagged as a fallback."""
        d_km = haversine_km(ward.lat, ward.lon, DELHI_CENTER[0], DELHI_CENTER[1])
        # inner city => denser roads; construction/industrial vary less predictably
        road = max(0.0, 1.0 - d_km / 25.0)
        return {
            "road_proximity": round(road, 3),
            "construction_proximity": 0.3,
            "stack_proximity": 0.3,
            "raw": {"distance_center_km": round(d_km, 2)},
            "source": "heuristic_fallback",
        }

    # -- proximity cache --------------------------------------------------
    def _prox_path(self, ward_id: str):
        safe = ward_id.replace("/", "_")
        return self._cache_dir / f"{safe}.json"

    def _read_prox_cache(self, ward_id: str) -> Optional[dict]:
        p = self._prox_path(ward_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _write_prox_cache(self, ward_id: str, data: dict) -> None:
        try:
            self._prox_path(ward_id).write_text(json.dumps(data))
        except OSError:
            pass


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = EARTH_RADIUS_M / 1000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
