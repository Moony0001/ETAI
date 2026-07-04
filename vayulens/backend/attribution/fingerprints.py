"""Per-source evidence scoring from normalised features.

Each source has a *fingerprint* — a weighted set of features (config.py). The
evidence for a source is the weighted sum of its feature values, clipped at 0
(negative/absent signals contribute nothing). We also keep each feature's
contribution so the UI can say *why* a source scored high.
"""

from __future__ import annotations

from backend.config import ATTRIBUTION_WEIGHTS, SOURCES

# human-friendly labels for the driver explanations
FEATURE_LABELS: dict[str, str] = {
    "uvai_z": "UV aerosol index elevated",
    "co_z": "CO elevated",
    "upwind_fire": "upwind active fires",
    "coarse_ratio_fine": "fine-mode dominated (low PM10/PM2.5)",
    "seasonal_biomass": "stubble-burning season",
    "no2_z": "NO2 elevated",
    "rush_fit": "tracks rush-hour cycle",
    "road_proximity": "near dense roads",
    "coarse_ratio_dust": "coarse dominated (high PM10/PM2.5)",
    "low_rh": "dry air",
    "construction_proximity": "near construction",
    "daytime": "daytime re-suspension",
    "so2_z": "SO2 elevated",
    "stack_proximity": "near industrial area",
    "steady": "flat, non-diurnal signal",
    "common_mode": "shared across all stations (regional)",
}


def score_source(
    source: str, features: dict[str, float]
) -> tuple[float, dict[str, float]]:
    """Evidence for one source + per-feature contribution (weight * value)."""
    weights = ATTRIBUTION_WEIGHTS[source]
    contributions: dict[str, float] = {}
    total = 0.0
    for feat, w in weights.items():
        val = features.get(feat, 0.0)
        contrib = w * val
        contributions[feat] = contrib
        total += contrib
    return max(0.0, total), contributions


def score_all(
    features: dict[str, float]
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Evidence per source + contributions per source, over all SOURCES."""
    evidence: dict[str, float] = {}
    contribs: dict[str, dict[str, float]] = {}
    for source in SOURCES:
        ev, contrib = score_source(source, features)
        evidence[source] = ev
        contribs[source] = contrib
    return evidence, contribs


def top_feature_for(source: str, contribs: dict[str, float]) -> str:
    """The single feature that contributed most to a source's evidence."""
    if not contribs:
        return ""
    feat = max(contribs, key=lambda f: contribs[f])
    return FEATURE_LABELS.get(feat, feat)
