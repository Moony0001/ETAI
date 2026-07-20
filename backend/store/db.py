"""DuckDB analytics store — schema, connection, upserts.

Persistence is best-effort: a DB error must never crash the pipeline, so every
write is wrapped defensively. JSON-ish columns (shares/masses/...) are stored as
text via json.dumps for portability across DuckDB versions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

import duckdb

from backend.config import DB_PATH
from backend.models import Fire, MetPoint, Reading, Station, WardAttribution

logger = logging.getLogger("vayulens.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    station_id VARCHAR PRIMARY KEY,
    name VARCHAR, lat DOUBLE, lon DOUBLE, provider VARCHAR, synthetic BOOLEAN
);
CREATE TABLE IF NOT EXISTS readings (
    station_id VARCHAR, parameter VARCHAR, value DOUBLE, unit VARCHAR,
    timestamp TIMESTAMP, lat DOUBLE, lon DOUBLE, synthetic BOOLEAN,
    PRIMARY KEY (station_id, parameter, timestamp)
);
CREATE TABLE IF NOT EXISTS fires (
    lat DOUBLE, lon DOUBLE, frp DOUBLE, timestamp TIMESTAMP,
    confidence VARCHAR, source VARCHAR, synthetic BOOLEAN,
    PRIMARY KEY (lat, lon, timestamp)
);
CREATE TABLE IF NOT EXISTS met (
    lat DOUBLE, lon DOUBLE, timestamp TIMESTAMP,
    wind_speed DOUBLE, wind_dir DOUBLE, u DOUBLE, v DOUBLE,
    blh DOUBLE, rh DOUBLE, temp DOUBLE, precip DOUBLE, synthetic BOOLEAN,
    PRIMARY KEY (lat, lon, timestamp)
);
CREATE TABLE IF NOT EXISTS attributions (
    ward_id VARCHAR, ward_name VARCHAR, timestamp TIMESTAMP, lat DOUBLE, lon DOUBLE,
    pm25_obs DOUBLE, pm25_baseline DOUBLE, excess DOUBLE,
    shares VARCHAR, masses VARCHAR, evidence VARCHAR,
    confidence DOUBLE, top_drivers VARCHAR, data_completeness DOUBLE,
    data_source VARCHAR, notes VARCHAR,
    PRIMARY KEY (ward_id, timestamp)
);
CREATE TABLE IF NOT EXISTS attribution_batches (
    date VARCHAR PRIMARY KEY,
    meta VARCHAR,
    geojson VARCHAR,
    created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trajectories (
    ward_id VARCHAR,
    date VARCHAR,
    level VARCHAR,
    payload VARCHAR,
    created_at TIMESTAMP,
    PRIMARY KEY (ward_id, date, level)
);
"""


def connect(path: Optional[str] = None) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the DuckDB store and ensure the schema exists."""
    con = duckdb.connect(str(path or DB_PATH))
    con.execute(_SCHEMA)
    return con


def init_db(path: Optional[str] = None) -> None:
    con = connect(path)
    con.close()


# ---------------------------------------------------------------------------
def save_stations(con: duckdb.DuckDBPyConnection, stations: Iterable[Station]) -> None:
    rows = [(s.station_id, s.name, s.lat, s.lon, s.provider, s.synthetic) for s in stations]
    if not rows:
        return
    try:
        con.executemany(
            "INSERT OR REPLACE INTO stations VALUES (?, ?, ?, ?, ?, ?)", rows
        )
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] save_stations failed: %s", exc)


def save_readings(con: duckdb.DuckDBPyConnection, readings: Iterable[Reading]) -> None:
    rows = [
        (r.station_id, r.parameter, r.value, r.unit, r.timestamp, r.lat, r.lon, r.synthetic)
        for r in readings
    ]
    if not rows:
        return
    try:
        con.executemany(
            "INSERT OR REPLACE INTO readings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] save_readings failed: %s", exc)


def save_fires(con: duckdb.DuckDBPyConnection, fires: Iterable[Fire]) -> None:
    rows = [
        (f.lat, f.lon, f.frp, f.timestamp, f.confidence, f.source, f.synthetic)
        for f in fires
    ]
    if not rows:
        return
    try:
        con.executemany("INSERT OR REPLACE INTO fires VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] save_fires failed: %s", exc)


def save_met(con: duckdb.DuckDBPyConnection, met: Iterable[MetPoint]) -> None:
    rows = [
        (m.lat, m.lon, m.timestamp, m.wind_speed, m.wind_dir, m.u, m.v,
         m.blh, m.rh, m.temp, m.precip, m.synthetic)
        for m in met
    ]
    if not rows:
        return
    try:
        con.executemany(
            "INSERT OR REPLACE INTO met VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] save_met failed: %s", exc)


def save_attribution(con: duckdb.DuckDBPyConnection, a: WardAttribution) -> None:
    try:
        con.execute(
            "INSERT OR REPLACE INTO attributions VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                a.ward_id, a.ward_name, a.timestamp, a.lat, a.lon,
                a.pm25_obs, a.pm25_baseline, a.excess,
                json.dumps(a.shares), json.dumps(a.masses), json.dumps(a.evidence),
                a.confidence, json.dumps(a.top_drivers), a.data_completeness,
                a.data_source, json.dumps(a.notes),
            ],
        )
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] save_attribution failed: %s", exc)


def save_attribution_batch(
    con: duckdb.DuckDBPyConnection, date: str, meta: dict, geojson: dict
) -> None:
    """Cache a whole-city batch result keyed by date so demo re-runs are instant."""
    try:
        con.execute(
            "INSERT OR REPLACE INTO attribution_batches VALUES (?, ?, ?, ?)",
            [date, json.dumps(meta), json.dumps(geojson), datetime.utcnow()],
        )
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] save_attribution_batch failed: %s", exc)


def load_attribution_batch(
    con: duckdb.DuckDBPyConnection, date: str
) -> Optional[tuple[dict, dict]]:
    """Return (meta, geojson) for a cached batch date, or None if not cached."""
    try:
        row = con.execute(
            "SELECT meta, geojson FROM attribution_batches WHERE date = ?",
            [date],
        ).fetchone()
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] load_attribution_batch failed: %s", exc)
        return None
    if not row:
        return None
    try:
        return json.loads(row[0]), json.loads(row[1])
    except (json.JSONDecodeError, TypeError):
        return None


def save_trajectory(
    con: duckdb.DuckDBPyConnection, ward_id: str, date: str, level: str, payload: dict
) -> None:
    """Cache a trajectory response keyed by (ward_id, date, wind level)."""
    try:
        con.execute(
            "INSERT OR REPLACE INTO trajectories VALUES (?, ?, ?, ?, ?)",
            [ward_id, date, level, json.dumps(payload), datetime.now(timezone.utc)],
        )
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] save_trajectory failed: %s", exc)


def load_trajectory(
    con: duckdb.DuckDBPyConnection, ward_id: str, date: str, level: str
) -> Optional[dict]:
    try:
        row = con.execute(
            "SELECT payload FROM trajectories WHERE ward_id = ? AND date = ? AND level = ?",
            [ward_id, date, level],
        ).fetchone()
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] load_trajectory failed: %s", exc)
        return None
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def latest_attribution(
    con: duckdb.DuckDBPyConnection, ward_id: str
) -> Optional[WardAttribution]:
    try:
        row = con.execute(
            "SELECT * FROM attributions WHERE ward_id = ? ORDER BY timestamp DESC LIMIT 1",
            [ward_id],
        ).fetchone()
    except duckdb.Error as exc:  # pragma: no cover
        logger.warning("[store] latest_attribution failed: %s", exc)
        return None
    if not row:
        return None
    cols = [
        "ward_id", "ward_name", "timestamp", "lat", "lon", "pm25_obs", "pm25_baseline",
        "excess", "shares", "masses", "evidence", "confidence", "top_drivers",
        "data_completeness", "data_source", "notes",
    ]
    d = dict(zip(cols, row))
    for j in ("shares", "masses", "evidence", "top_drivers", "notes"):
        d[j] = json.loads(d[j]) if d[j] else ([] if j in ("top_drivers", "notes") else {})
    return WardAttribution(**d)
