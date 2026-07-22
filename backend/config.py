"""Central configuration for vayulens.

Everything that a reviewer might call a "magic number" lives here so the
attribution heuristic stays transparent, tunable, and defensible. Nothing in
the enrichment/attribution code should hard-code a threshold or weight — it
should read it from this module.

Env vars are loaded once here via python-dotenv; adapters import the keys from
this module rather than reading os.environ directly.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
GEO_DIR = DATA_DIR / "geo"
WARD_GEOJSON = GEO_DIR / "delhi_wards.geojson"

load_dotenv(ROOT_DIR / ".env")

RAW_DIR.mkdir(parents=True, exist_ok=True)
GEO_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("VAYULENS_DB_PATH", str(DATA_DIR / "vayulens.duckdb")))

# --------------------------------------------------------------------------
# Secrets / API config (empty string => adapter skips gracefully)
# --------------------------------------------------------------------------
OPENAQ_API_KEY = os.getenv("OPENAQ_API_KEY", "").strip()
FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

OPENAQ_BASE_URL = os.getenv("OPENAQ_BASE_URL", "https://api.openaq.org/v3")

# --------------------------------------------------------------------------
# LLM narration. The model only EXPLAINS numbers the engine already produced —
# it never computes or alters attribution. Provider is swappable via
# NARRATION_PROVIDER; any provider failure (missing key, timeout, auth, SDK
# absent) degrades to deterministic text. "none" forces the fallback.
# Default is Gemini (free, no card at AI Studio).
# --------------------------------------------------------------------------
NARRATION_PROVIDER = os.getenv("NARRATION_PROVIDER", "gemini").strip().lower()
NARRATION_MAX_TOKENS = int(os.getenv("NARRATION_MAX_TOKENS", "700"))
# Generation timeout. Generous by default because local models (Ollama) are slow
# cold — this is fine since warm-caching runs offline; live clicks serve from cache.
NARRATION_TIMEOUT_S = float(os.getenv("NARRATION_TIMEOUT_S", "120"))

# Gemini — google-genai SDK; free key at https://aistudio.google.com/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Groq — OpenAI-compatible; free key at https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Anthropic — https://console.anthropic.com/
NARRATION_MODEL = os.getenv("NARRATION_MODEL", "claude-sonnet-4-6")

# Bedrock (optional) — boto3 + standard AWS credential chain
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "amazon.nova-micro-v1:0")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Ollama (optional, fully local) — no key
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
FIRMS_AVAILABILITY_URL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv"
FIRMS_SOURCE = os.getenv("FIRMS_SOURCE", "VIIRS_SNPP_NRT")          # near-real-time (~last 2 months)
# Standard-processing (science-quality) VIIRS: the historical archive (2012-present,
# ~2 months behind). Used automatically for episode dates the NRT feed can't reach.
FIRMS_ARCHIVE_SOURCE = os.getenv("FIRMS_ARCHIVE_SOURCE", "VIIRS_SNPP_SP")
OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# Archived FORECAST runs (2022-present) — the only Open-Meteo dataset that carries
# pressure-level winds for historical dates. ERA5 archive exposes surface winds only.
OPENMETEO_HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

HTTP_TIMEOUT_S = 30.0

# --------------------------------------------------------------------------
# Geography — Delhi
# --------------------------------------------------------------------------
# [west, south, east, north]
DELHI_BBOX: tuple[float, float, float, float] = (76.84, 28.40, 77.35, 28.88)
DELHI_CENTER: tuple[float, float] = (28.6139, 77.2090)  # (lat, lon)

# Fallback grid when no ward polygon file is present (~1 km cells).
GRID_CELL_KM = 1.0

# --------------------------------------------------------------------------
# Pollutant parameters we care about
# --------------------------------------------------------------------------
PARAMETERS: tuple[str, ...] = ("pm25", "pm10", "no2", "so2", "co", "o3")

# OpenAQ v3 exposes these canonical parameter names; map to our short codes.
OPENAQ_PARAM_MAP: dict[str, str] = {
    "pm25": "pm25",
    "pm10": "pm10",
    "no2": "no2",
    "so2": "so2",
    "co": "co",
    "o3": "o3",
}

# --------------------------------------------------------------------------
# Baseline (clean-day excess)
# --------------------------------------------------------------------------
BASELINE_PERCENTILE = 10          # 10th percentile over the trailing window
BASELINE_WINDOW_DAYS = 14         # trailing window length
BASELINE_MIN_SAMPLES = 12         # need at least this many points to trust it
BASELINE_FALLBACK_UGM3 = 35.0     # WHO-ish clean-ish floor if we can't compute one

# --------------------------------------------------------------------------
# Feature normalisation constants
# --------------------------------------------------------------------------
# z-scores are clipped to [0, Z_CLIP] then divided by Z_CLIP -> feature in [0,1]
Z_CLIP = 3.0

# PM10/PM2.5 coarse ratio interpretation (PM10 >= PM2.5 so ratio >= 1)
COARSE_RATIO_DUST_LO = 1.8        # ratio at/above which "dust-like" signal starts
COARSE_RATIO_DUST_HI = 4.0        # ratio at which dust signal saturates to 1.0
COARSE_RATIO_FINE_HI = 1.8        # ratio at/below which "combustion-like" starts
COARSE_RATIO_FINE_LO = 1.1        # ratio at which fine (biomass) signal saturates

# Relative humidity: dust is favoured when the air is dry
RH_DUST_MAX = 60.0                # % — at/below RH_DUST_LO gives full dust support
RH_DUST_LO = 20.0

# Diurnal rush-hour fit (traffic) — local hours (Asia/Kolkata assumed for diurnal shape)
RUSH_MORNING = (7, 10)
RUSH_EVENING = (17, 21)

# Daytime window (dust re-suspension / construction)
DAYTIME_PEAK = (11, 16)

# Steadiness (industrial): coefficient of variation of ward PM2.5 over the window.
# Low CV => steady => industrial-supporting.
STEADY_CV_REF = 0.6

# Upwind fire evidence saturation (biomass): score/(score+HALF) -> 0..1
FIRE_SCORE_HALF = 500.0

# Seasonality of biomass (stubble) burning in NW India
BIOMASS_PEAK_MONTHS = (10, 11)    # Oct, Nov
BIOMASS_SHOULDER_MONTHS = (5, 6, 9, 12)  # some wheat-residue + early/late
BIOMASS_OFFSEASON_FACTOR = 0.30   # biomass never fully zero (fires can happen)

# --------------------------------------------------------------------------
# ATTRIBUTION WEIGHTS  (the differentiator — keep readable & defensible)
# Each source's evidence = sum_f weight[source][feature] * feature_value[f]
# feature_value is normalised to [0,1] in enrichment/features.py.
# Weights are relative importances; they do NOT need to sum to 1.
# --------------------------------------------------------------------------
ATTRIBUTION_WEIGHTS: dict[str, dict[str, float]] = {
    "biomass": {
        "uvai_z": 1.2,            # UV aerosol index up (absorbing smoke)  [TROPOMI]
        "co_z": 0.8,              # CO up (incomplete combustion)
        "upwind_fire": 2.0,       # air parcel passed over active fires
        "coarse_ratio_fine": 0.8,  # low PM10/PM2.5 (fine-mode dominated)
        "seasonal_biomass": 1.0,  # Oct-Nov stubble season
    },
    "traffic": {
        "no2_z": 1.6,             # NO2 up (fresh vehicular exhaust)
        "co_z": 0.6,
        "rush_fit": 1.4,          # concentration tracks rush-hour shape
        "road_proximity": 1.0,    # ward near dense road network
    },
    "dust": {
        "coarse_ratio_dust": 1.8,  # high PM10/PM2.5 (coarse dominated)
        "low_rh": 0.9,            # dry air favours re-suspension
        "construction_proximity": 1.1,
        "daytime": 0.7,           # convective/traffic-churn re-suspension peaks midday
    },
    "industrial": {
        "so2_z": 1.8,             # SO2 up (fossil / smelting stacks)
        "stack_proximity": 1.3,   # ward near industrial areas
        "steady": 1.0,            # flat, non-diurnal signal
    },
    "regional": {
        "common_mode": 2.0,       # portion of excess shared across ALL stations
    },
}

# All source names, canonical order for display.
SOURCES: tuple[str, ...] = ("biomass", "traffic", "dust", "industrial", "regional")

# --------------------------------------------------------------------------
# ENFORCEMENT RANKING  (monitoring -> intervention)
# Ranks wards by pollution a LOCAL inspector can actually act on, not raw AQI.
# Advected stubble smoke (biomass) and the shared regional floor are excluded —
# a Delhi inspector can't fix a Punjab field. Weights here, never inline.
# --------------------------------------------------------------------------
# Sources a local body can enforce against (their attributed mass is "actionable").
ENFORCEMENT_LOCAL_SOURCES: tuple[str, ...] = ("traffic", "dust", "industrial")
# Sources that need REGIONAL coordination instead (not locally fixable).
ENFORCEMENT_REGIONAL_SOURCES: tuple[str, ...] = ("biomass", "regional")
# score = actionable_mass * ((1-w) + w*confidence);  w in [0,1].
ENFORCEMENT_CONFIDENCE_WEIGHT = 1.0
# A ward needs at least this much locally-attributable excess (µg/m³) to be queued.
ENFORCEMENT_MIN_ACTIONABLE_UGM3 = 3.0
# A ward counts as "regionally dominated" (advected, not locally fixable) when the
# biomass+regional share is at least this. The regional list surfaces the worst
# (highest-excess) such wards — the contrast to the enforcement queue.
ENFORCEMENT_REGIONAL_DOMINANCE = 0.55
# How many wards to surface in the "regional coordination" contrast list.
ENFORCEMENT_REGIONAL_LIST_SIZE = 8
# Recommended action per dominant local source.
ENFORCEMENT_ACTIONS: dict[str, str] = {
    "traffic": "PUC drive / congestion & idling enforcement",
    "dust": "water-sprinkling + C&D site audit",
    "industrial": "stack emission inspection",
}

# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------
# confidence = W_PEAK * peakedness + W_COMPLETE * data_completeness
CONFIDENCE_W_PEAK = 0.6           # 1 - normalised entropy of the shares
CONFIDENCE_W_COMPLETE = 0.4       # fraction of expected inputs actually present
# Inputs we hope to have; completeness = present / len(this)
EXPECTED_INPUTS: tuple[str, ...] = (
    "pm25", "pm10", "no2", "so2", "co", "wind", "fires", "geo",
)

# --------------------------------------------------------------------------
# Back-trajectory
# --------------------------------------------------------------------------
TRAJECTORY_HOURS = 24
TRAJECTORY_DT_H = 1
FIRE_KERNEL_DIST_KM = 50.0        # exp(-dist/50)
FIRE_KERNEL_AGE_H = 12.0          # exp(-age/12)
FIRE_MAX_DIST_KM = 150.0          # ignore fires beyond this from the path (perf)

# Advect the parcel through the BOUNDARY LAYER, not 10 m surface wind — surface
# drag badly under-represents long-range smoke transport. ~850 hPa (~1.5 km) is the
# usual level for stubble-smoke advection. Set to "10m" to disable. Sampled from
# Open-Meteo pressure-level winds; falls back to 10 m wherever the level is absent.
TRAJECTORY_PRESSURE_LEVEL = "850hPa"          # "10m" | "925hPa" | "900hPa" | "850hPa" | ...
TRAJECTORY_LEVELS_AVAILABLE = ("10m", "925hPa", "900hPa", "850hPa", "700hPa")

# --------------------------------------------------------------------------
# Proximity (geodata) — search radii in metres
# --------------------------------------------------------------------------
ROAD_BUFFER_M = 300
INDUSTRIAL_BUFFER_M = 1500
CONSTRUCTION_BUFFER_M = 800
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

EARTH_RADIUS_M = 6_371_000.0
