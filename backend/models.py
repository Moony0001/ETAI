"""Pydantic v2 models — the typed contract at every boundary.

Adapters return lists of these; enrichment consumes them; the attribution
engine emits WardAttribution; the API serialises them straight to JSON.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Parameter = Literal["pm25", "pm10", "no2", "so2", "co", "o3"]
SourceName = Literal["biomass", "traffic", "dust", "industrial", "regional"]


class Station(BaseModel):
    """A ground monitoring station (OpenAQ location)."""

    station_id: str
    name: str
    lat: float
    lon: float
    provider: Optional[str] = None
    # parameter short-code -> OpenAQ sensor id (used to pull history)
    sensors: dict[str, int] = Field(default_factory=dict)
    synthetic: bool = False


class Reading(BaseModel):
    """A single pollutant measurement at a station at a time."""

    station_id: str
    parameter: Parameter
    value: float
    unit: str
    timestamp: datetime
    lat: float
    lon: float
    synthetic: bool = False


class Fire(BaseModel):
    """An active-fire detection (NASA FIRMS)."""

    lat: float
    lon: float
    frp: float  # Fire Radiative Power, MW
    timestamp: datetime
    confidence: Optional[str] = None
    source: str = "VIIRS_SNPP_NRT"
    synthetic: bool = False


class MetPoint(BaseModel):
    """Hourly meteorology at a point (Open-Meteo)."""

    lat: float
    lon: float
    timestamp: datetime
    wind_speed: float          # m/s
    wind_dir: float            # degrees, meteorological (direction wind blows FROM)
    u: float                   # eastward wind component, m/s
    v: float                   # northward wind component, m/s
    blh: Optional[float] = None    # boundary-layer height, m
    rh: Optional[float] = None     # relative humidity, %
    temp: Optional[float] = None   # 2 m temperature, °C
    precip: Optional[float] = None # mm
    level: str = "10m"             # wind level this point's u/v are sampled at
    synthetic: bool = False


class TropomiPoint(BaseModel):
    """Sentinel-5P column tracer at a point (STUB — None until GEE wired)."""

    lat: float
    lon: float
    timestamp: datetime
    no2: Optional[float] = None      # mol/m^2
    so2: Optional[float] = None
    co: Optional[float] = None
    uvai: Optional[float] = None     # UV Aerosol Index (unitless)


class TrajectoryStep(BaseModel):
    """One hourly node on a back-trajectory path."""

    hour_back: int
    lat: float
    lon: float
    timestamp: datetime


class WardAttribution(BaseModel):
    """The headline output: what drove this ward's pollution excess, and how sure we are."""

    ward_id: str
    ward_name: str
    timestamp: datetime
    lat: float
    lon: float

    pm25_obs: float
    pm25_baseline: float
    excess: float                       # max(0, obs - baseline), µg/m³

    shares: dict[str, float]            # source -> fraction of excess (sums to ~1)
    masses: dict[str, float]            # source -> µg/m³ attributed
    evidence: dict[str, float]          # raw pre-normalisation evidence (inspectable)
    confidence: float                   # 0..1
    top_drivers: list[str]              # sources sorted desc by share

    data_completeness: float            # 0..1
    data_source: Literal["real", "partial", "synthetic_fallback"] = "real"
    notes: list[str] = Field(default_factory=list)


class WardSummary(BaseModel):
    """Lightweight ward descriptor for the /wards listing."""

    ward_id: str
    ward_name: str
    lat: float
    lon: float
