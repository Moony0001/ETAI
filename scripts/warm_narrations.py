"""Pre-generate and cache every narration for a demo date, so the LIVE demo makes
ZERO narration API calls — everything is served from the DuckDB cache.

Usage:
    uv run python scripts/warm_narrations.py                 # 2024-11-08, top 10 wards
    uv run python scripts/warm_narrations.py --date 2024-11-18 --top-n 15
    NARRATION_PROVIDER=gemini uv run python scripts/warm_narrations.py

It warms, for each of the top-N enforcement-queue wards (plus the city-centre ward
most likely to be clicked): the ward explanation + EN/HI advisory, and — once — the
whole enforcement-rationale set. After this runs, start the API with
NARRATION_PROVIDER=none and every warmed ward still shows full narrations from cache;
only un-warmed wards fall back. That is the demo-safety guarantee.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import narration  # noqa: E402
from backend.adapters.geodata import GeoDataAdapter  # noqa: E402
from backend.config import DELHI_CENTER, TRAJECTORY_PRESSURE_LEVEL  # noqa: E402
from backend.enforcement import rank_enforcement  # noqa: E402
from backend.pipeline import run_attribution_batch  # noqa: E402
from backend.store import db  # noqa: E402


def _load_or_compute_batch(con, t, date_str):
    cached = db.load_attribution_batch(con, date_str)
    if cached is not None:
        print(f"  batch: cache hit for {date_str}")
        return cached
    print(f"  batch: computing whole-city attribution for {date_str} (first run, slow)…")
    result = run_attribution_batch(t=t)
    db.save_attribution_batch(con, date_str, result.meta, result.geojson)
    return result.meta, result.geojson


def main() -> None:
    ap = argparse.ArgumentParser(description="Warm the narration cache for a demo date.")
    ap.add_argument("--date", default="2024-11-08", help="YYYY-MM-DD (default: locked demo episode)")
    ap.add_argument("--top-n", type=int, default=10, help="how many enforcement wards to warm")
    args = ap.parse_args()

    t = datetime.strptime(args.date, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    date_str = t.strftime("%Y-%m-%d")

    print("=" * 66)
    print(f"  warming narrations · date {date_str} · provider {narration.provider()} "
          f"· live={narration.available()}")
    print("=" * 66)

    con = db.connect()
    _meta, geojson = _load_or_compute_batch(con, t, date_str)
    features = geojson["features"]
    by_id = {f["properties"]["ward_id"]: f["properties"] for f in features}

    queue, _regional = rank_enforcement(features, limit=args.top_n)

    # wards to warm: the enforcement queue + the central ward (likely first click)
    central = GeoDataAdapter().ward_at(*DELHI_CENTER)
    ward_ids = [e["ward_id"] for e in queue]
    if central and central.ward_id not in ward_ids:
        ward_ids.append(central.ward_id)

    print(f"\n  warming {len(ward_ids)} ward narrations…")
    llm = fb = 0
    for i, wid in enumerate(ward_ids, 1):
        props = by_id.get(wid)
        if props is None:
            continue
        traj = db.load_trajectory(con, wid, date_str, TRAJECTORY_PRESSURE_LEVEL)
        t0 = time.time()
        result = narration.ward_narration(props, trajectory=traj)
        db.save_narration(con, "ward", wid, date_str, result)
        llm += result["source"] == "llm"
        fb += result["source"] == "fallback"
        print(f"    [{i:>2}/{len(ward_ids)}] {wid:<14} {result['source']:<8} {time.time()-t0:5.1f}s")

    print(f"\n  warming enforcement rationales (top {args.top_n}, one call)…")
    t0 = time.time()
    rationales = narration.enforcement_rationales(queue)
    db.save_narration(con, "enforcement", f"limit{args.top_n}", date_str,
                      {"date": date_str, "rationales": rationales})
    r_llm = sum(1 for v in rationales.values() if v.get("source") == "llm")
    print(f"    {len(rationales)} rationales ({r_llm} llm / {len(rationales) - r_llm} fallback) "
          f"in {time.time()-t0:.1f}s")

    con.close()
    print("\n" + "=" * 66)
    print(f"  DONE · wards: {llm} llm / {fb} fallback · cached in DuckDB (kind=ward|enforcement).")
    print("  Verify demo-safety:  restart the API with NARRATION_PROVIDER=none —")
    print("  these wards still serve full narrations from cache; others fall back.")
    print("=" * 66)


if __name__ == "__main__":
    main()
