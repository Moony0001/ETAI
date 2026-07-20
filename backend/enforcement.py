"""Enforcement ranking — turn attribution into a prioritised action queue.

Raw AQI tells you *where* it's bad; this tells you *where a local inspector can do
something about it*. We rank wards by locally-attributable pollution mass —
excess weighted by the traffic + dust + industrial shares — and deliberately
EXCLUDE biomass (advected stubble smoke) and the regional common-mode, because no
Delhi field team can fix a Punjab stubble fire. Those wards go on a separate
"regional coordination" list instead. All weights come from config.py.
"""

from __future__ import annotations

from backend.config import (
    ENFORCEMENT_ACTIONS,
    ENFORCEMENT_CONFIDENCE_WEIGHT,
    ENFORCEMENT_LOCAL_SOURCES,
    ENFORCEMENT_MIN_ACTIONABLE_UGM3,
    ENFORCEMENT_REGIONAL_DOMINANCE,
    ENFORCEMENT_REGIONAL_LIST_SIZE,
    ENFORCEMENT_REGIONAL_SOURCES,
)


def actionable_mass(props: dict) -> float:
    """µg/m³ of a ward's excess attributed to locally-enforceable sources."""
    masses = props.get("masses", {})
    return float(sum(masses.get(s, 0.0) for s in ENFORCEMENT_LOCAL_SOURCES))


def _score(am: float, confidence: float) -> float:
    w = ENFORCEMENT_CONFIDENCE_WEIGHT
    return am * ((1.0 - w) + w * float(confidence or 0.0))


def _dominant_local(shares: dict) -> str | None:
    best = max(ENFORCEMENT_LOCAL_SOURCES, key=lambda s: shares.get(s, 0.0))
    return best if shares.get(best, 0.0) > 0.0 else None


def _centroid(feature: dict) -> tuple[float, float]:
    """Approximate polygon centroid (mean of the exterior ring) as (lat, lon)."""
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    ring = coords[0] if coords else []
    if not ring:
        return 0.0, 0.0
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return sum(ys) / len(ys), sum(xs) / len(xs)


def rank_enforcement(
    features: list[dict], limit: int = 20
) -> tuple[list[dict], list[dict]]:
    """Return (queue, regional):

    queue    — wards ranked by confidence-weighted actionable mass, each with a
               recommended action; the deploy-here list.
    regional — a short contrast list of high-excess wards that are biomass/regional
               dominated, i.e. NOT locally actionable.
    """
    queue: list[dict] = []
    regional: list[dict] = []

    for f in features:
        p = f.get("properties", {})
        excess = float(p.get("excess", 0.0) or 0.0)
        if excess <= 0.0:
            continue
        shares = p.get("shares", {})
        am = actionable_mass(p)
        regional_share = sum(shares.get(s, 0.0) for s in ENFORCEMENT_REGIONAL_SOURCES)

        # The two lists are independent lenses, not mutually exclusive: a severe
        # stubble day can leave a ward both queue-worthy (some local mass to act on)
        # AND regionally dominated (most of its burden is advected).
        if am >= ENFORCEMENT_MIN_ACTIONABLE_UGM3:
            lat, lon = _centroid(f)
            dl = _dominant_local(shares)
            queue.append(
                {
                    "ward_id": p.get("ward_id"),
                    "name": p.get("name"),
                    "lat": round(lat, 5),
                    "lon": round(lon, 5),
                    "actionable_mass": round(am, 1),
                    "actionable_frac": round(am / excess, 3) if excess else 0.0,
                    "score": round(_score(am, p.get("confidence", 0.0)), 2),
                    "dominant_local_source": dl,
                    "action": ENFORCEMENT_ACTIONS.get(dl, "site audit"),
                    "confidence": p.get("confidence"),
                    "excess": excess,
                    "aqi": p.get("aqi"),
                    "aqi_band": p.get("aqi_band"),
                    "shares": shares,
                }
            )
        if regional_share >= ENFORCEMENT_REGIONAL_DOMINANCE:
            lat, lon = _centroid(f)
            dom = "biomass" if shares.get("biomass", 0.0) >= shares.get("regional", 0.0) else "regional"
            regional.append(
                {
                    "ward_id": p.get("ward_id"),
                    "name": p.get("name"),
                    "lat": round(lat, 5),
                    "lon": round(lon, 5),
                    "excess": excess,
                    "dominant_source": dom,
                    "regional_share": round(regional_share, 3),
                }
            )

    queue.sort(key=lambda e: e["score"], reverse=True)
    for i, e in enumerate(queue):
        e["rank"] = i + 1
    regional.sort(key=lambda e: e["excess"], reverse=True)
    return queue[:limit], regional[:ENFORCEMENT_REGIONAL_LIST_SIZE]
