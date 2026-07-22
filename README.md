# vayulens 🌬️🔎

**AI-powered urban air-quality _source attribution_ for Indian cities.**

Not another AQI dashboard. vayulens attributes each pollution spike to its likely
**source** — stubble burning (biomass) / traffic / construction dust / industrial /
regional background — by fusing **real ground sensors, active-fire data, meteorology,
and satellite tracers** through a transparent, explainable receptor model.

This repo is a **fast vertical slice**: real data flowing end-to-end for **one city
(Delhi)**, one attributed ward, on a runnable pipeline. Backend correctness is the
priority; the frontend is a thin placeholder.

---

## What makes it different

For a ward `w` at time `t`:

```
excess      = max(0, pm25_observed - pm25_baseline)      # baseline = 10th pct, trailing window
evidence_s  = Σ_features  weight[s][feature] · feature_value     # per source s, from its fingerprint
shares_s    = evidence_s / Σ evidence
mass_s      = shares_s · excess
confidence  = w1·peakedness(shares) + w2·data_completeness
```

Source **fingerprints** (all weights live in [`backend/config.py`](backend/config.py),
nothing buried in code):

| Source | Evidence it looks for |
|---|---|
| **biomass** | UVAI↑, CO↑, **upwind active fires**, low PM10/PM2.5 ratio, Oct–Nov season |
| **traffic** | NO2↑, CO↑, diurnal **rush-hour** fit, road-density proximity |
| **dust** | high PM10/PM2.5 ratio, low RH, construction proximity, daytime |
| **industrial** | SO2↑, stack proximity, steady (non-diurnal) signal |
| **regional** | the spatial **common-mode** shared across all stations |

The **back-trajectory** engine steps an air parcel backward hour-by-hour along the real
wind field; if the path sweeps over active fires (the Delhi→Punjab stubble corridor),
that's physical evidence for biomass. It's exportable as GeoJSON — the demo money-shot.

---

## Quickstart (one command)

```bash
# 1. install uv  ->  https://docs.astral.sh/uv/
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. install deps into a managed venv
uv sync

# 3. run the end-to-end smoke test on REAL data (Delhi)
uv run python scripts/smoke_test.py
```

No API keys? It still runs. **Open-Meteo is keyless**, so real wind + the back-trajectory
always work. OpenAQ/FIRMS gracefully fall back to a **clearly-labelled synthetic** Delhi
scenario (output shows `data_source: synthetic_fallback`) so you see the full engine
working. Add keys to get real ground + fire data — no code changes needed.

### Run the API

```bash
uv run uvicorn backend.api:app --reload
# http://127.0.0.1:8000/docs
#   GET /health
#   GET /wards
#   GET /attribution?date=YYYY-MM-DD   -> whole-city { meta, geojson } (map choropleth; cached per date)
#   GET /attribution/{ward_id}?date=…  -> one ward's full WardAttribution
#   GET /trajectory?ward_id=…&date=…   -> back-trajectory + contributing fires (GeoJSON)
#   GET /trajectory/{ward_id}?date=…   -> same, path form
```

The batch `GET /attribution` fetches the shared snapshot (stations, fires, wind) **once
per (city, date)** and attributes every ward from it, then caches the FeatureCollection
in DuckDB keyed by date so demo re-runs are instant. `date` is optional everywhere and
defaults to latest/today.

### Run the frontend (command-centre map)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173  (calls the API via the /api Vite proxy)
```

Dark MapLibre + deck.gl. Ward choropleth coloured by **dominant source** (toggle to CPCB
AQI severity), a click-through attribution panel, the Delhi→Punjab back-trajectory
corridor, date presets for the Nov-2024 stubble window, and LIVE/DEMO provenance badges.
API base is the `VITE_API_URL` env var (see `frontend/.env.example`); unset → the Vite
`/api` proxy, so there's no CORS setup in dev.

---

## API keys (all free, all optional to _start_)

Copy `.env.example` → `.env` and fill what you have. Missing keys → that adapter logs a
warning and skips (never crashes).

| Env var | Source | Get it |
|---|---|---|
| `OPENAQ_API_KEY` | OpenAQ v3 ground sensors | https://explore.openaq.org/register |
| `FIRMS_MAP_KEY` | NASA FIRMS active fires | https://firms.modaps.eosdis.nasa.gov/api/area/ |
| _GEE auth_ | Sentinel-5P / TROPOMI | `uv sync --extra gee` then `earthengine authenticate` |

### LLM narration (optional, swappable provider)

Plain-language explanations + EN/Hindi advisory. Pick a provider with
`NARRATION_PROVIDER` (default **gemini**); any failure or missing key falls back to
deterministic text, so it never breaks. `none` forces the fallback.

| Provider | Env | Get it | Notes |
|---|---|---|---|
| **gemini** (default) | `GEMINI_API_KEY` | https://aistudio.google.com/apikey | **free, no card**; `gemini-flash-lite-latest` (fast, fluent Hindi, large free quota) |
| **groq** | `GROQ_API_KEY` | https://console.groq.com/keys | **free, no card**; `llama-3.1-8b-instant` |
| **anthropic** | `ANTHROPIC_API_KEY` | https://console.anthropic.com/ | `claude-sonnet-4-6` |
| **bedrock** | AWS creds + `AWS_REGION` | standard AWS chain | `amazon.nova-micro-v1:0` (Converse) |
| **ollama** | — (local) | `ollama serve` + `ollama pull llama3.2` | fully offline, no key |

**Warm the cache before a demo** so live clicks make zero API calls:

```bash
uv run python scripts/warm_narrations.py --date 2024-11-08 --top-n 10
# then the demo can run with NARRATION_PROVIDER=none — warmed wards serve from cache.
```

---

## Ward polygons

Drop a GeoJSON of Delhi ward boundaries at:

```
data/geo/delhi_wards.geojson
```

The loader auto-detects common property keys (`Ward_No`, `Ward_Name`, `wardcode`, …).
**Where to get real polygons (MCD's 250 wards):**

- Datameet / community mirrors of MCD ward boundaries (search "Delhi MCD wards geojson"),
- OpenCity / Delhi open-data portals,
- or derive from the Election Commission / SDMC ward shapefiles and convert with
  `ogr2ogr -f GeoJSON delhi_wards.geojson wards.shp`.

**No file?** The pipeline auto-generates a ~1 km grid over `DELHI_BBOX` so everything
still runs — grid cells get ids like `grid_r04_c05`.

---

## Architecture

```
adapters/     openaq · firms · openmeteo · tropomi(stub) · geodata   (AbstractSourceAdapter)
enrichment/   baseline (excess) · features (normalised signals) · trajectory (back-traj + fire)
attribution/  fingerprints (per-source evidence) · engine (shares/masses/confidence)
forecast/     persistence baseline (real) + LightGBM (stub)
store/        DuckDB schema + upserts
pipeline.py   fetch -> enrich -> attribute for one ward/window
api.py        FastAPI endpoints
```

Design rules: every source is an `AbstractSourceAdapter` returning typed pydantic
records; raw pulls are cached under `data/raw/`; every threshold/weight is in
`config.py`. It's a **transparent heuristic receptor model, not ML** — readable and
defensible on purpose.

---

## What's real vs stubbed in this pass

- **Real / runnable:** config, models, OpenAQ/FIRMS/Open-Meteo/geodata adapters,
  baseline, features, trajectory, fingerprints, engine, DuckDB store, pipeline, batch
  city-wide attribution + CPCB AQI, FastAPI, and the deck.gl command-centre frontend.
- **Stubbed (clear TODOs):** `tropomi.py` (needs `earthengine authenticate`; returns
  `None` gracefully so the pipeline runs without it) and the LightGBM forecast
  (persistence baseline works).

## Roadmap / TODO

- Wire TROPOMI via GEE → UVAI/CO/SO2/NO2 columns flow straight into the fingerprints.
- Real 250-ward polygons in `data/geo/` (grid fallback works today).
- Implement `LightGBMForecaster` for 6–24h source-resolved forecasts.
- Enforcement ranking panel + LLM natural-language explanations (Anthropic key present).
- Subtle wind-vector overlay sampled from the met field.

> ⚠️ vayulens is a decision-support prototype. The receptor model is a defensible
> heuristic, not a regulatory-grade chemical transport model.
