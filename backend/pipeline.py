"""Orchestration: fetch -> enrich -> attribute for one ward / time window.

This is the vertical slice. It fuses ground sensors (OpenAQ), active fires
(FIRMS), meteorology (Open-Meteo, real & keyless) and — when authenticated —
satellite tracers (TROPOMI), then runs the transparent receptor model.

Graceful degradation is a first-class concern:
  * Open-Meteo always works (no key) -> real wind + back-trajectory.
  * If OpenAQ/FIRMS keys are absent, we fall back to a clearly-labelled
    synthetic Delhi scenario so the whole chain still executes end-to-end.
    The output carries data_source="synthetic_fallback" and explanatory notes.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from backend.adapters.firms import FirmsAdapter
from backend.adapters.geodata import GeoDataAdapter, Ward, haversine_km
from backend.adapters.openaq import OpenAQAdapter
from backend.adapters.openmeteo import OpenMeteoAdapter, nearest_in_time
from backend.adapters.tropomi import TropomiAdapter
from backend.attribution.engine import attribute
from backend.config import (
    BASELINE_WINDOW_DAYS,
    DELHI_BBOX,
    DELHI_CENTER,
    PARAMETERS,
    TRAJECTORY_PRESSURE_LEVEL,
)
from backend.enrichment.baseline import excess as compute_excess
from backend.enrichment.baseline import regional_floor, station_baseline
from backend.enrichment.features import FeatureInputs, build_features
from backend.enrichment.trajectory import (
    back_trajectory,
    biomass_evidence,
    trajectory_geojson,
)
from backend.aqi import pm25_to_aqi
from backend.models import Fire, MetPoint, Reading, Station, TrajectoryStep, WardAttribution
from backend.store import db

logger = logging.getLogger("vayulens.pipeline")


@dataclass
class PipelineResult:
    attribution: WardAttribution
    path: list[TrajectoryStep]
    contributors: list[dict]
    trajectory_geojson: dict
    met_series: list[MetPoint] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)
    fires: list[Fire] = field(default_factory=list)
    n_stations: int = 0
    n_fires: int = 0


@dataclass
class BatchResult:
    """City-wide attribution snapshot: one GeoJSON FeatureCollection + metadata."""

    meta: dict
    geojson: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _latest_by_station(readings: list[Reading]) -> dict[str, dict[str, float]]:
    """station_id -> {param -> most-recent value}."""
    out: dict[str, dict[str, tuple[datetime, float]]] = {}
    for r in readings:
        cur = out.setdefault(r.station_id, {})
        if r.parameter not in cur or r.timestamp > cur[r.parameter][0]:
            cur[r.parameter] = (r.timestamp, r.value)
    return {sid: {p: tv[1] for p, tv in d.items()} for sid, d in out.items()}


def _panel_stats(
    latest: dict[str, dict[str, float]]
) -> dict[str, tuple[float, float]]:
    """param -> (mean, std) across all stations (spatial distribution)."""
    stats: dict[str, tuple[float, float]] = {}
    for p in PARAMETERS:
        vals = [d[p] for d in latest.values() if p in d and d[p] is not None]
        if len(vals) >= 2:
            stats[p] = (float(np.mean(vals)), float(np.std(vals)))
        elif vals:
            stats[p] = (float(vals[0]), 0.0)
    return stats


def _assign_stations(ward: Ward, stations: list[Station]) -> list[Station]:
    """Stations inside the ward, else the nearest few within ~8 km."""
    from shapely.geometry import Point

    inside = [s for s in stations if ward.geometry.contains(Point(s.lon, s.lat))]
    if inside:
        return inside
    ranked = sorted(stations, key=lambda s: haversine_km(ward.lat, ward.lon, s.lat, s.lon))
    near = [s for s in ranked if haversine_km(ward.lat, ward.lon, s.lat, s.lon) <= 8.0]
    return near[:3] or ranked[:1]


def _ward_values(
    assigned: list[Station], latest: dict[str, dict[str, float]]
) -> dict[str, float]:
    """Mean of assigned stations' latest values, per parameter."""
    values: dict[str, float] = {}
    for p in PARAMETERS:
        vals = [latest[s.station_id][p] for s in assigned
                if s.station_id in latest and p in latest[s.station_id]]
        if vals:
            values[p] = float(np.mean(vals))
    return values


# ---------------------------------------------------------------------------
# Synthetic fallback (clearly labelled)
# ---------------------------------------------------------------------------
def _synthetic_ground(
    t: datetime,
) -> tuple[list[Station], list[Reading], dict[str, list[Reading]]]:
    """A plausible Delhi scenario used only when OpenAQ has no key.

    Spatial variety across stations (a traffic hotspot, an industrial SO2 zone,
    dusty outskirts, a shared regional floor) so z-scores and the regional
    common-mode are meaningful. History windows let the baseline + diurnal
    features run.
    """
    rng = random.Random(int(t.timestamp()) // 3600)
    west, south, east, north = DELHI_BBOX
    # (name, dlat_frac, dlon_frac, profile)
    specs = [
        ("Anand Vihar (traffic)", 0.72, 0.85, "traffic"),
        ("ITO (traffic)", 0.55, 0.55, "traffic"),
        ("Okhla Phase-2 (industrial)", 0.30, 0.80, "industrial"),
        ("Wazirpur (industrial)", 0.75, 0.45, "industrial"),
        ("Dwarka (dust)", 0.30, 0.15, "dust"),
        ("Rohini (dust)", 0.82, 0.30, "dust"),
        ("Najafgarh (outskirt)", 0.18, 0.10, "background"),
        ("Lodhi Road (background)", 0.48, 0.52, "background"),
        ("RK Puram (mixed)", 0.42, 0.42, "mixed"),
        ("Punjabi Bagh (mixed)", 0.66, 0.40, "mixed"),
        ("Mundka (dust)", 0.70, 0.12, "dust"),
        ("Nehru Nagar (traffic)", 0.40, 0.60, "traffic"),
    ]
    regional_floor_pm25 = 62.0  # shared common-mode across the city

    stations: list[Station] = []
    latest: list[Reading] = []
    history: dict[str, list[Reading]] = {}

    for i, (name, fy, fx, profile) in enumerate(specs):
        lat = south + fy * (north - south)
        lon = west + fx * (east - west)
        sid = f"synthetic-{i:02d}"
        stations.append(
            Station(station_id=sid, name=name, lat=lat, lon=lon,
                    provider="synthetic", synthetic=True)
        )

        # profile-driven local increments on top of the regional floor
        local = {"traffic": 40, "industrial": 30, "dust": 55, "mixed": 35,
                 "background": 8}[profile]
        pm25 = regional_floor_pm25 + local + rng.uniform(-6, 6)
        coarse = {"traffic": 1.5, "industrial": 1.7, "dust": 3.1, "mixed": 1.9,
                  "background": 1.8}[profile]
        pm10 = pm25 * coarse
        no2 = {"traffic": 70, "industrial": 35, "dust": 25, "mixed": 45,
               "background": 20}[profile] + rng.uniform(-5, 5)
        so2 = {"traffic": 8, "industrial": 45, "dust": 6, "mixed": 12,
               "background": 5}[profile] + rng.uniform(-2, 2)
        co = {"traffic": 1.6, "industrial": 1.1, "dust": 0.6, "mixed": 1.0,
              "background": 0.5}[profile] + rng.uniform(-0.1, 0.1)
        o3 = 30 + rng.uniform(-8, 8)

        for param, val, unit in (
            ("pm25", pm25, "µg/m³"), ("pm10", pm10, "µg/m³"),
            ("no2", no2, "µg/m³"), ("so2", so2, "µg/m³"),
            ("co", co, "mg/m³"), ("o3", o3, "µg/m³"),
        ):
            latest.append(Reading(station_id=sid, parameter=param, value=round(val, 1),
                                  unit=unit, timestamp=t, lat=lat, lon=lon, synthetic=True))

        # 72h PM2.5 history with a diurnal (rush-heavy) shape + clean nights
        hist: list[Reading] = []
        for h in range(72, 0, -1):
            ts = t - timedelta(hours=h)
            local_h = (ts.hour + 5.5) % 24
            diurnal = (
                18 * math.exp(-((local_h - 9) ** 2) / 6)     # morning rush
                + 22 * math.exp(-((local_h - 20) ** 2) / 8)  # evening rush
            ) if profile in ("traffic", "mixed") else 8 * math.sin(local_h / 24 * 2 * math.pi)
            clean_night = -18 if 2 <= local_h <= 5 else 0
            val = max(15.0, regional_floor_pm25 * 0.6 + local * 0.5 + diurnal
                      + clean_night + rng.uniform(-5, 5))
            hist.append(Reading(station_id=sid, parameter="pm25", value=round(val, 1),
                               unit="µg/m³", timestamp=ts, lat=lat, lon=lon, synthetic=True))
        history[sid] = hist

    return stations, latest, history


def _synthetic_fires_along_path(path: list[TrajectoryStep], t: datetime) -> list[Fire]:
    """Seed a few synthetic fires near the UPWIND half of the real trajectory.

    This demonstrates corridor detection when FIRMS has no key. Fires are placed
    only if the parcel actually travelled a meaningful distance, and are flagged
    synthetic. With a real FIRMS_MAP_KEY + NW winter winds these are replaced by
    genuine Punjab/Haryana stubble detections.
    """
    if len(path) < 8:
        return []
    origin = path[0]
    far = path[-1]
    if haversine_km(origin.lat, origin.lon, far.lat, far.lon) < 30:
        return []  # calm/short path — no honest corridor to seed
    rng = random.Random(20260704)
    fires: list[Fire] = []
    for node in path[len(path) // 2:]:  # upwind half only
        for _ in range(2):
            fires.append(
                Fire(
                    lat=node.lat + rng.uniform(-0.08, 0.08),
                    lon=node.lon + rng.uniform(-0.08, 0.08),
                    frp=rng.uniform(15, 90),
                    timestamp=t - timedelta(hours=rng.uniform(2, 20)),
                    confidence="synthetic",
                    source="SYNTHETIC",
                    synthetic=True,
                )
            )
    return fires


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_attribution(
    ward_id: Optional[str] = None,
    t: Optional[datetime] = None,
    *,
    allow_synthetic: bool = True,
    persist: bool = True,
) -> PipelineResult:
    """Run the full fetch->enrich->attribute slice for one ward at time t."""
    t = (t or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    notes: list[str] = []

    geo = GeoDataAdapter()
    wards = geo.load_wards()
    if not wards:
        raise RuntimeError("No wards available (grid generation failed).")
    ward = geo.get_ward(ward_id) if ward_id else None
    if ward is None:
        ward = geo.ward_at(*DELHI_CENTER) or wards[0]
        if ward_id:
            notes.append(f"ward_id '{ward_id}' not found; used '{ward.ward_id}'.")

    # --- Meteorology (real, keyless) + back-trajectory -------------------
    met = OpenMeteoAdapter()
    met_series = met.series_window(ward.lat, ward.lon, t)
    ward_met = nearest_in_time(met_series, t)
    if ward_met is None:
        notes.append("Open-Meteo returned no wind; trajectory unavailable.")
    # Ward features use surface met (rh etc.); the trajectory advects through the
    # boundary layer (config TRAJECTORY_PRESSURE_LEVEL) for realistic transport.
    path = (
        back_trajectory(ward.lat, ward.lon, t, met.make_wind_fn(t, level=TRAJECTORY_PRESSURE_LEVEL))
        if ward_met
        else []
    )

    # --- Active fires (real FIRMS: NRT or SP archive, else synthetic) ----
    firms = FirmsAdapter()
    fires: list[Fire] = []
    fires_provenance = "none"
    if firms.available:
        # widen the box NW toward the stubble belt
        w, s, e, n = DELHI_BBOX
        wide = (w - 3.0, s - 0.5, e + 0.5, n + 2.5)
        fires, fires_provenance = firms.fetch_for_date(wide, t)
    if not fires and allow_synthetic:
        fires = _synthetic_fires_along_path(path, t)
        if fires:
            fires_provenance = "synthetic_fallback"
            notes.append(f"FIRMS unavailable for this date: seeded {len(fires)} synthetic fires "
                         "upwind along the real trajectory (demo).")
    if fires_provenance == "archive":
        notes.append("Fires: FIRMS standard-processing archive (science-quality) for this date.")

    fire_score_raw, contributors = biomass_evidence(path, fires, t0=t)

    # --- Ground sensors (real OpenAQ, else synthetic) --------------------
    openaq = OpenAQAdapter()
    data_source = "real"
    pm25_history: dict[str, list[Reading]] = {}

    if openaq.available:
        stations = openaq.list_stations(bbox=DELHI_BBOX)
        latest_readings: list[Reading] = []
        for st in stations:
            latest_readings.extend(openaq.latest_for_station(st))
        # PM2.5 history for the baseline (only for assigned stations, later)
        if not stations:
            notes.append("OpenAQ returned no stations in bbox.")
    else:
        stations, latest_readings = [], []

    if not stations and allow_synthetic:
        stations, latest_readings, pm25_history = _synthetic_ground(t)
        data_source = "synthetic_fallback"
        notes.append("OpenAQ key absent: using synthetic ground-sensor scenario.")
    elif not stations:
        raise RuntimeError("No ground stations and synthetic fallback disabled.")
    elif not firms.available or any(f.synthetic for f in fires):
        # real ground data, but a satellite/fire channel is missing or synthetic
        data_source = "partial"

    latest = _latest_by_station(latest_readings)
    panel = _panel_stats(latest)
    assigned = _assign_stations(ward, stations)

    # real OpenAQ: pull PM2.5 history for the assigned stations (for baseline)
    if openaq.available and data_source != "synthetic_fallback":
        dfrom = t - timedelta(days=BASELINE_WINDOW_DAYS)
        for st in assigned:
            sid_sensor = st.sensors.get("pm25")
            if sid_sensor is not None:
                pm25_history[st.station_id] = openaq.history(
                    sid_sensor, "pm25", st, dfrom, t
                )

    ward_values = _ward_values(assigned, latest)
    pm25_obs = ward_values.get("pm25")
    if pm25_obs is None:
        notes.append("No PM2.5 at/near ward; excess set to 0.")
        pm25_obs = 0.0

    # --- Baseline & excess ----------------------------------------------
    ward_hist: list[Reading] = []
    for st in assigned:
        ward_hist.extend(pm25_history.get(st.station_id, []))
    pm25_baseline = station_baseline(ward_hist, "pm25")
    excess = compute_excess(pm25_obs, pm25_baseline)
    window_pm25 = [(r.timestamp, r.value) for r in ward_hist]

    # --- Regional common-mode (per-station excess floor) -----------------
    station_excesses: dict[str, float] = {}
    for st in stations:
        obs = latest.get(st.station_id, {}).get("pm25")
        if obs is None:
            continue
        base = station_baseline(pm25_history.get(st.station_id, []), "pm25")
        station_excesses[st.station_id] = compute_excess(obs, base)
    reg_floor = regional_floor(station_excesses)

    # --- Proximity + satellite ------------------------------------------
    proximity = geo.proximity(ward)
    tropomi = TropomiAdapter().sample(ward.lat, ward.lon, t)
    if tropomi is None:
        notes.append("TROPOMI/GEE not authenticated; satellite channel absent.")

    # --- Features + attribution -----------------------------------------
    bundle = build_features(
        FeatureInputs(
            t=t,
            ward_values=ward_values,
            panel_stats=panel,
            met=ward_met,
            window_pm25=window_pm25,
            fire_score_raw=fire_score_raw,
            proximity=proximity,
            ward_excess=excess,
            regional_floor=reg_floor,
            tropomi=tropomi,
        )
    )
    result = attribute(
        ward_id=ward.ward_id,
        ward_name=ward.ward_name,
        lat=ward.lat,
        lon=ward.lon,
        t=t,
        pm25_obs=pm25_obs,
        pm25_baseline=pm25_baseline,
        excess=excess,
        bundle=bundle,
        data_source=data_source,
        notes=notes,
    )

    # --- Persist (best-effort) ------------------------------------------
    if persist:
        try:
            con = db.connect()
            db.save_stations(con, stations)
            db.save_readings(con, latest_readings)
            db.save_fires(con, fires)
            db.save_met(con, met_series)
            db.save_attribution(con, result)
            con.close()
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            logger.warning("[pipeline] persistence skipped: %s", exc)

    geojson = trajectory_geojson(path, contributors)
    return PipelineResult(
        attribution=result,
        path=path,
        contributors=contributors,
        trajectory_geojson=geojson,
        met_series=met_series,
        stations=stations,
        fires=fires,
        n_stations=len(stations),
        n_fires=len(fires),
    )


# ---------------------------------------------------------------------------
# Fast trajectory-only path (the corridor money-shot, no attribution)
# ---------------------------------------------------------------------------
def run_trajectory(
    ward: Ward,
    t: Optional[datetime] = None,
    level: Optional[str] = None,
    *,
    use_cache: bool = True,
) -> dict:
    """Back-trajectory + contributing fires for one ward, WITHOUT the attribution
    pipeline (no ground fetch, no baseline, no DuckDB attribution writes).

    This is what the /trajectory endpoint calls — it only needs the wind field and
    the fires, so it is ~5x faster than routing through run_attribution. Cached in
    DuckDB per (ward_id, date, level) so a re-click is instant.
    """
    t = (t or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    level = level or TRAJECTORY_PRESSURE_LEVEL
    date_str = t.strftime("%Y-%m-%d")

    if use_cache:
        try:
            con = db.connect()
            cached = db.load_trajectory(con, ward.ward_id, date_str, level)
            con.close()
            if cached is not None:
                return {**cached, "cached": True}
        except Exception as exc:  # noqa: BLE001 - cache is best-effort
            logger.warning("[pipeline] trajectory cache read skipped: %s", exc)

    met = OpenMeteoAdapter()
    wind_fn = met.make_wind_fn(t, level=level)
    m0 = wind_fn(ward.lat, ward.lon, t)
    actual_level = m0.level if m0 else level
    path = back_trajectory(ward.lat, ward.lon, t, wind_fn) if m0 else []

    firms = FirmsAdapter()
    fires: list[Fire] = []
    fires_provenance = "none"
    if firms.available:
        w, s, e, n = DELHI_BBOX
        wide = (w - 3.0, s - 0.5, e + 0.5, n + 2.5)
        fires, fires_provenance = firms.fetch_for_date(wide, t)
    if not fires:
        fires = _synthetic_fires_along_path(path, t)
        if fires:
            fires_provenance = "synthetic_fallback"

    _score, contributors = biomass_evidence(path, fires, t0=t, with_contributors=True)
    transport_km = (
        haversine_km(path[0].lat, path[0].lon, path[-1].lat, path[-1].lon)
        if len(path) >= 2
        else 0.0
    )
    payload = {
        "ward_id": ward.ward_id,
        "date": date_str,
        "level": actual_level,
        "fires_provenance": fires_provenance,
        "hours": len(path) - 1 if path else 0,
        "n_contributing_fires": len(contributors),
        "transport_km": round(transport_km, 1),
        "geojson": trajectory_geojson(path, contributors),
    }
    try:
        con = db.connect()
        db.save_trajectory(con, ward.ward_id, date_str, actual_level, payload)
        con.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pipeline] trajectory cache write skipped: %s", exc)
    return {**payload, "cached": False}


# ---------------------------------------------------------------------------
# Batch attribution (whole-city snapshot for the map choropleth)
# ---------------------------------------------------------------------------
def _top_driver_text(attr: WardAttribution) -> str:
    """One-line 'Primary driver: <reason>' string from the ranked drivers.

    top_drivers entries look like 'biomass (41%): upwind active fires'; we keep
    the human reason after the colon. When there is no positive local evidence
    (everything went to regional background) we say so plainly.
    """
    if attr.excess <= 0:
        return "Primary driver: no excess above the clean-day baseline"
    if not attr.top_drivers:
        return "Primary driver: regional background"
    head = attr.top_drivers[0]
    reason = head.split(": ", 1)[1] if ": " in head else head
    return f"Primary driver: {reason}"


def _ground_ready_station_histories(
    openaq: OpenAQAdapter,
    stations: list[Station],
    t: datetime,
    *,
    fetch_history: bool,
) -> dict[str, list[Reading]]:
    """PM2.5 history per station for baselines, fetched ONCE for the whole city.

    Best-effort: OpenAQ rate limits aggressively, so any station whose history is
    unavailable simply falls back to the clean-day baseline downstream (identical
    to the single-ward path). Disk-cached, so repeat runs are cheap.
    """
    if not fetch_history or not openaq.available:
        return {}
    dfrom = t - timedelta(days=BASELINE_WINDOW_DAYS)
    histories: dict[str, list[Reading]] = {}
    for st in stations:
        sid_sensor = st.sensors.get("pm25")
        if sid_sensor is None:
            continue
        hist = openaq.history(sid_sensor, "pm25", st, dfrom, t)
        if hist:
            histories[st.station_id] = hist
    return histories


def run_attribution_batch(
    t: Optional[datetime] = None,
    *,
    allow_synthetic: bool = True,
    fetch_history: bool = False,
    allow_network_proximity: bool = False,
) -> BatchResult:
    """Fetch the shared (city, date) snapshot ONCE, then attribute every ward.

    Shared data — ground stations + latest readings, active fires, the wind
    field, per-station baselines and the regional common-mode — is pulled a
    single time. The per-ward loop then only does cheap local work (station
    assignment, back-trajectory, feature build, attribution). This is the
    fetch-once/attribute-many refactor the map needs; nothing in the attribution
    math changes, it is just hoisted out of the per-ward path.

    Baseline policy: `fetch_history` defaults to False. OpenAQ's per-sensor
    /hours endpoint rate-limits hard and returns nothing for older dates, so the
    city sweep uses the clean-day fallback baseline (identical to what the
    single-ward path falls back to in practice). The /attribution/{ward_id}
    endpoint still does the deeper per-station history fetch for a precise
    baseline. `allow_network_proximity` is likewise False so 2700 cells never
    fan out into Overpass calls; proximity resolves from cache or the
    distance-to-centre heuristic.
    """
    from shapely.geometry import mapping

    t = (t or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)

    geo = GeoDataAdapter()
    wards = geo.load_wards()
    if not wards:
        raise RuntimeError("No wards available (grid generation failed).")

    # --- Wind field (real, keyless) -------------------------------------
    # Surface wind for ward features (rh presence); boundary-layer wind for the
    # trajectory so smoke transport reaches the real upwind source region.
    met = OpenMeteoAdapter()
    surface_wind_fn = met.make_wind_fn(t)
    traj_wind_fn = met.make_wind_fn(t, level=TRAJECTORY_PRESSURE_LEVEL)

    # --- Active fires (shared): real FIRMS (NRT or SP archive), else synthetic ---
    firms = FirmsAdapter()
    fires: list[Fire] = []
    fires_provenance = "none"
    if firms.available:
        w, s, e, n = DELHI_BBOX
        wide = (w - 3.0, s - 0.5, e + 0.5, n + 2.5)
        fires, fires_provenance = firms.fetch_for_date(wide, t)
    if not fires and allow_synthetic:
        ref_path = back_trajectory(*DELHI_CENTER, t, traj_wind_fn)
        fires = _synthetic_fires_along_path(ref_path, t)
        if fires:
            fires_provenance = "synthetic_fallback"
    fires_live = fires_provenance in ("live", "archive")

    # --- Ground sensors (shared): real OpenAQ, else synthetic scenario ---
    openaq = OpenAQAdapter()
    ground_live = False
    stations: list[Station] = []
    latest_readings: list[Reading] = []
    pm25_history: dict[str, list[Reading]] = {}

    if openaq.available:
        stations = openaq.list_stations(bbox=DELHI_BBOX)
        for st in stations:
            latest_readings.extend(openaq.latest_for_station(st))
        ground_live = bool(stations)

    if not stations and allow_synthetic:
        stations, latest_readings, pm25_history = _synthetic_ground(t)
    elif ground_live:
        pm25_history = _ground_ready_station_histories(
            openaq, stations, t, fetch_history=fetch_history
        )

    if ground_live and fires_live:
        data_source = "real"
    elif ground_live:
        data_source = "partial"
    else:
        data_source = "synthetic_fallback"

    latest = _latest_by_station(latest_readings)
    panel = _panel_stats(latest)

    # --- Regional common-mode (city-wide, computed ONCE) -----------------
    station_baselines: dict[str, float] = {
        st.station_id: station_baseline(pm25_history.get(st.station_id, []), "pm25")
        for st in stations
    }
    station_excesses: dict[str, float] = {}
    for st in stations:
        obs = latest.get(st.station_id, {}).get("pm25")
        if obs is None:
            continue
        station_excesses[st.station_id] = compute_excess(
            obs, station_baselines[st.station_id]
        )
    reg_floor = regional_floor(station_excesses)

    tropomi_adapter = TropomiAdapter()  # built once; sample() returns None if unauth

    # --- Per-ward loop over the single snapshot --------------------------
    features_out: list[dict] = []
    for ward in wards:
        assigned = _assign_stations(ward, stations)
        ward_values = _ward_values(assigned, latest)
        pm25_obs = ward_values.get("pm25")
        if pm25_obs is None:
            pm25_obs = 0.0

        ward_hist: list[Reading] = []
        for st in assigned:
            ward_hist.extend(pm25_history.get(st.station_id, []))
        pm25_baseline = station_baseline(ward_hist, "pm25")
        excess = compute_excess(pm25_obs, pm25_baseline)
        window_pm25 = [(r.timestamp, r.value) for r in ward_hist]

        ward_met = surface_wind_fn(ward.lat, ward.lon, t)
        path = back_trajectory(ward.lat, ward.lon, t, traj_wind_fn) if ward_met else []
        fire_score_raw, _contribs = biomass_evidence(path, fires, t0=t, with_contributors=False)

        proximity = geo.proximity(ward, allow_network=allow_network_proximity)
        tropomi = tropomi_adapter.sample(ward.lat, ward.lon, t)

        bundle = build_features(
            FeatureInputs(
                t=t,
                ward_values=ward_values,
                panel_stats=panel,
                met=ward_met,
                window_pm25=window_pm25,
                fire_score_raw=fire_score_raw,
                proximity=proximity,
                ward_excess=excess,
                regional_floor=reg_floor,
                tropomi=tropomi,
            )
        )
        attr = attribute(
            ward_id=ward.ward_id,
            ward_name=ward.ward_name,
            lat=ward.lat,
            lon=ward.lon,
            t=t,
            pm25_obs=pm25_obs,
            pm25_baseline=pm25_baseline,
            excess=excess,
            bundle=bundle,
            data_source=data_source,
            notes=[],
        )
        aqi = pm25_to_aqi(attr.pm25_obs)
        features_out.append(
            {
                "type": "Feature",
                "geometry": mapping(ward.geometry),
                "properties": {
                    "ward_id": attr.ward_id,
                    "name": attr.ward_name,
                    "pm25": attr.pm25_obs,
                    "aqi": aqi.aqi,
                    "aqi_band": aqi.band,
                    "excess": attr.excess,
                    "shares": attr.shares,
                    "masses": attr.masses,
                    "confidence": attr.confidence,
                    "top_driver_text": _top_driver_text(attr),
                },
            }
        )

    meta = {
        "city": "Delhi",
        "date": t.strftime("%Y-%m-%d"),
        "timestamp": t.isoformat(),
        "provenance": {
            "ground": "live" if ground_live else "synthetic_fallback",
            "fires": fires_provenance,
            "wind": "live",
        },
        "wind_level": TRAJECTORY_PRESSURE_LEVEL,
        "station_count": len(stations),
        "fire_count": len(fires),
        "ward_count": len(features_out),
        "is_grid": bool(wards and wards[0].is_grid),
    }
    geojson = {"type": "FeatureCollection", "features": features_out}
    return BatchResult(meta=meta, geojson=geojson)
