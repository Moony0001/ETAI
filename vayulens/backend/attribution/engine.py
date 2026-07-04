"""Attribution engine: evidence -> shares, masses, confidence, drivers.

This is a transparent heuristic receptor model, NOT machine learning. Given the
per-source evidence from fingerprints.py and the ward's PM2.5 excess, it splits
that excess into source masses and reports how confident the split is.

    shares_s   = evidence_s / sum(evidence)
    mass_s     = shares_s * excess
    confidence = W_PEAK * peakedness(shares) + W_COMPLETE * data_completeness
                 where peakedness = 1 - normalised_entropy(shares)
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from backend.config import (
    CONFIDENCE_W_COMPLETE,
    CONFIDENCE_W_PEAK,
    EXPECTED_INPUTS,
    SOURCES,
)
from backend.attribution.fingerprints import score_all, top_feature_for
from backend.enrichment.features import FeatureBundle
from backend.models import WardAttribution


def _normalised_entropy(shares: dict[str, float]) -> float:
    """Shannon entropy of the share distribution, normalised to [0,1]."""
    ps = [p for p in shares.values() if p > 0]
    if len(ps) <= 1:
        return 0.0
    h = -sum(p * math.log(p) for p in ps)
    return h / math.log(len(SOURCES))


def _completeness(present: dict[str, bool]) -> float:
    have = sum(1 for k in EXPECTED_INPUTS if present.get(k))
    return have / len(EXPECTED_INPUTS)


def attribute(
    ward_id: str,
    ward_name: str,
    lat: float,
    lon: float,
    t: datetime,
    pm25_obs: float,
    pm25_baseline: float,
    excess: float,
    bundle: FeatureBundle,
    data_source: str = "real",
    notes: Optional[list[str]] = None,
) -> WardAttribution:
    """Fuse per-source evidence into a WardAttribution for ward w at time t."""
    evidence, contribs = score_all(bundle.features)
    total = sum(evidence.values())

    if total <= 1e-9:
        # No positive evidence anywhere — attribute everything to regional
        # background rather than inventing a local source.
        shares = {s: (1.0 if s == "regional" else 0.0) for s in SOURCES}
    else:
        shares = {s: evidence[s] / total for s in SOURCES}

    masses = {s: shares[s] * excess for s in SOURCES}

    peakedness = 1.0 - _normalised_entropy(shares)
    completeness = _completeness(bundle.present)
    confidence = CONFIDENCE_W_PEAK * peakedness + CONFIDENCE_W_COMPLETE * completeness

    ranked = sorted(SOURCES, key=lambda s: shares[s], reverse=True)
    top_drivers: list[str] = []
    for s in ranked:
        if shares[s] < 0.05:
            break
        reason = top_feature_for(s, contribs[s])
        top_drivers.append(f"{s} ({shares[s] * 100:.0f}%): {reason}")

    return WardAttribution(
        ward_id=ward_id,
        ward_name=ward_name,
        timestamp=t,
        lat=lat,
        lon=lon,
        pm25_obs=round(pm25_obs, 1),
        pm25_baseline=round(pm25_baseline, 1),
        excess=round(excess, 1),
        shares={s: round(shares[s], 4) for s in SOURCES},
        masses={s: round(masses[s], 2) for s in SOURCES},
        evidence={s: round(evidence[s], 4) for s in SOURCES},
        confidence=round(confidence, 3),
        top_drivers=top_drivers,
        data_completeness=round(completeness, 3),
        data_source=data_source,  # type: ignore[arg-type]
        notes=notes or [],
    )
