"""End-to-end smoke test — runs the Delhi slice against REAL APIs.

  uv run python scripts/smoke_test.py            # default central ward
  uv run python scripts/smoke_test.py --ward grid_r04_c05

Pulls real Open-Meteo wind (keyless), real OpenAQ stations + FIRMS fires when
keys are present (else a clearly-labelled synthetic fallback), runs enrichment
+ attribution for one ward, and prints source shares / masses / confidence plus
a one-line back-trajectory summary.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# allow `python scripts/smoke_test.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import (  # noqa: E402
    ANTHROPIC_API_KEY,
    DELHI_CENTER,
    FIRMS_MAP_KEY,
    OPENAQ_API_KEY,
)
from backend.adapters.geodata import GeoDataAdapter  # noqa: E402
from backend.pipeline import run_attribution  # noqa: E402

BAR = "═" * 72


def _bar(frac: float, width: int = 24) -> str:
    filled = int(round(frac * width))
    return "█" * filled + "·" * (width - filled)


def _print_keys() -> None:
    def mark(v: str) -> str:
        return "present ✅" if v else "MISSING ⚠️  (adapter will skip / synthesize)"
    print(BAR)
    print("  vayulens smoke test — Delhi source attribution slice")
    print(BAR)
    print(f"  OPENAQ_API_KEY   : {mark(OPENAQ_API_KEY)}")
    print(f"  FIRMS_MAP_KEY    : {mark(FIRMS_MAP_KEY)}")
    print(f"  ANTHROPIC_API_KEY: {mark(ANTHROPIC_API_KEY)}")
    print(f"  Open-Meteo       : keyless ✅ (real wind/BLH/RH always available)")
    print(BAR)


def _print_attribution(res) -> None:
    a = res.attribution
    print()
    print(f"  Ward        : {a.ward_name}  [{a.ward_id}]")
    print(f"  Time (UTC)  : {a.timestamp:%Y-%m-%d %H:%M}")
    print(f"  Location    : {a.lat:.4f}, {a.lon:.4f}")
    print(f"  Data source : {a.data_source}")
    print(f"  Stations    : {res.n_stations}   Fires: {res.n_fires}")
    print()
    print(f"  PM2.5 observed : {a.pm25_obs:6.1f} µg/m³")
    print(f"  PM2.5 baseline : {a.pm25_baseline:6.1f} µg/m³  (10th pct, trailing window)")
    print(f"  Excess         : {a.excess:6.1f} µg/m³  <- split below")
    print()
    print("  SOURCE ATTRIBUTION OF EXCESS")
    print("  " + "-" * 68)
    print(f"  {'source':<12}{'share':>8}  {'mass µg/m³':>11}   {'evidence':>9}   bar")
    print("  " + "-" * 68)
    for s in sorted(a.shares, key=lambda k: a.shares[k], reverse=True):
        print(f"  {s:<12}{a.shares[s] * 100:>6.1f}% {a.masses[s]:>11.2f}   "
              f"{a.evidence[s]:>9.3f}   {_bar(a.shares[s])}")
    print("  " + "-" * 68)
    print(f"  Confidence        : {a.confidence:.2f}  "
          f"(peakedness + data completeness {a.data_completeness:.2f})")
    print()
    print("  TOP DRIVERS")
    for d in a.top_drivers:
        print(f"    • {d}")
    if a.notes:
        print()
        print("  NOTES")
        for n in a.notes:
            print(f"    - {n}")


def _print_trajectory(res) -> None:
    path = res.path
    print()
    print("  BACK-TRAJECTORY (24h, real Open-Meteo wind)")
    if not path:
        print("    (no wind data — trajectory unavailable)")
        return
    origin, far = path[0], path[-1]
    from backend.adapters.geodata import haversine_km

    dist = haversine_km(origin.lat, origin.lon, far.lat, far.lon)
    bearing = _compass(origin.lat, origin.lon, far.lat, far.lon)
    fire_w = sum(c["weight"] for c in res.contributors)
    print(f"    parcel traced {len(path) - 1}h back to "
          f"{far.lat:.3f},{far.lon:.3f} — {dist:.0f} km to the {bearing}")
    print(f"    contributing fires: {len(res.contributors)}  "
          f"(total upwind biomass score {fire_w:.1f})")
    if res.contributors:
        top = res.contributors[0]
        print(f"    strongest fire: FRP {top['frp']:.0f} MW, {top['dist_km']} km "
              f"from path, {top['age_h']}h old")


def _compass(lat1, lon1, lat2, lon2) -> str:
    import math

    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    brng = (math.degrees(math.atan2(y, x)) + 360) % 360
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((brng + 22.5) // 45) % 8]


def _print_todos() -> None:
    print()
    print(BAR)
    print("  NEXT STEPS")
    print(BAR)
    print("""  Map frontend (frontend/):
    - npm install && npm run dev  (Vite + React + deck.gl + maplibre)
    - it already calls /wards, /attribution, /trajectory; wire the ward
      polygons + a source-share bar and render the /trajectory GeoJSON as a
      line with fire points (the Delhi->Punjab corridor money-shot).

  TROPOMI / Sentinel-5P (backend/adapters/tropomi.py):
    - uv sync --extra gee ; earthengine authenticate ; set EE_PROJECT
    - once authed, UVAI+CO+SO2+NO2 columns flow automatically into the
      biomass/industrial/traffic fingerprints (no engine changes needed).

  Real data (replace the synthetic fallback):
    - add OPENAQ_API_KEY (https://explore.openaq.org/register)
    - add FIRMS_MAP_KEY  (https://firms.modaps.eosdis.nasa.gov/api/area/)
    - drop data/geo/delhi_wards.geojson for real 250-ward polygons (see README).

  Forecast (backend/forecast/baseline_model.py):
    - persistence baseline runs; implement LightGBMForecaster.train/predict.""")
    print(BAR)


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="vayulens Delhi smoke test")
    parser.add_argument("--ward", default=None, help="ward_id (default: central Delhi)")
    parser.add_argument("--no-persist", action="store_true", help="skip DuckDB writes")
    parser.add_argument(
        "--date",
        default=None,
        help="analyse a past day YYYY-MM-DD (uses REAL ERA5 archive wind). "
        "Try a stubble episode e.g. --date 2024-11-08 to light up the "
        "Delhi->Punjab corridor.",
    )
    args = parser.parse_args()

    _print_keys()

    t = None
    if args.date:
        t = datetime.strptime(args.date, "%Y-%m-%d").replace(
            hour=10, tzinfo=timezone.utc
        )
        print(f"  Analysing historical day: {args.date} (real ERA5 archive wind)")

    geo = GeoDataAdapter()
    ward_id = args.ward
    if ward_id is None:
        ward = geo.ward_at(*DELHI_CENTER)
        ward_id = ward.ward_id if ward else None

    print("  Running pipeline (fetch -> enrich -> attribute)...")
    res = run_attribution(ward_id=ward_id, t=t, persist=not args.no_persist)

    _print_attribution(res)
    _print_trajectory(res)
    _print_todos()
    print("\n  ✅ smoke test completed on real data.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
