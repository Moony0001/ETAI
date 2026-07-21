"""LLM narration — plain-language explanations of attribution the engine produced.

HARD RULE: the model never computes, changes, invents, or re-weights any number.
It only *explains* figures that are passed in as facts; the prompt says so and we
pass the computed values in. When ANTHROPIC_API_KEY is absent or the call fails,
every function degrades to deterministic text built from the same figures — the
panel is never empty and never stalls the demo. Set the key and it flips to live
with no code change.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.config import ANTHROPIC_API_KEY, NARRATION_MAX_TOKENS, NARRATION_MODEL

logger = logging.getLogger("vayulens.narration")

LOCAL_SOURCES = ("traffic", "dust", "industrial")
SOURCE_LABELS = {
    "biomass": "biomass burning",
    "traffic": "traffic",
    "dust": "dust",
    "industrial": "industrial",
    "regional": "regional background",
}

# Deterministic per-band health advisories (English + Hindi) used as the fallback
# and as grounding context for the LLM.
_BAND_ADVISORY = {
    "Good": ("Air quality is good — outdoor activity is fine for everyone.",
             "वायु गुणवत्ता अच्छी है — सभी के लिए बाहर की गतिविधियाँ ठीक हैं।"),
    "Satisfactory": ("Air quality is acceptable; unusually sensitive people should watch for symptoms.",
                     "वायु गुणवत्ता संतोषजनक है; अति-संवेदनशील लोग लक्षणों पर ध्यान दें।"),
    "Moderate": ("Sensitive groups should limit prolonged outdoor exertion.",
                 "संवेदनशील समूह लंबे समय तक बाहर का परिश्रम सीमित करें।"),
    "Poor": ("Reduce prolonged outdoor exertion; sensitive groups should stay indoors.",
             "लंबे समय तक बाहरी परिश्रम कम करें; संवेदनशील समूह घर के अंदर रहें।"),
    "Very Poor": ("Avoid outdoor exertion; wear an N95 outdoors and keep windows shut.",
                  "बाहरी परिश्रम से बचें; बाहर N95 मास्क पहनें और खिड़कियाँ बंद रखें।"),
    "Severe": ("Health alert: everyone should avoid outdoor activity; run an air purifier indoors.",
               "स्वास्थ्य चेतावनी: सभी बाहरी गतिविधि से बचें; घर के अंदर एयर प्यूरीफायर चलाएँ।"),
}


def available() -> bool:
    return bool(ANTHROPIC_API_KEY)


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # imported lazily so the app runs without the dep issue

        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _extract_json(text: str) -> Optional[dict]:
    """Parse a JSON object out of a model reply (tolerates ``` fences / prose)."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _dominant(shares: dict) -> str:
    if not shares:
        return "regional"
    return max(shares, key=lambda s: shares.get(s, 0.0))


def _local_frac(shares: dict) -> float:
    return sum(shares.get(s, 0.0) for s in LOCAL_SOURCES)


# ---------------------------------------------------------------------------
# Deterministic fallbacks (also used to ground the LLM)
# ---------------------------------------------------------------------------
def _fallback_ward(props: dict) -> dict:
    shares = props.get("shares", {}) or {}
    band = props.get("aqi_band", "Moderate")
    excess = props.get("excess", 0) or 0
    dom = _dominant(shares)
    dom_pct = round(shares.get(dom, 0.0) * 100)
    local = _local_frac(shares)
    if excess <= 0:
        explanation = (
            f"{props.get('name', 'This ward')} is at or below its clean-day baseline, "
            "so there is no attributable pollution excess right now."
        )
    else:
        advected = dom in ("biomass", "regional")
        framing = (
            "This is mostly advected/regional, so it needs regional coordination rather than a local inspection."
            if advected
            else "This is locally generated, so a local intervention can reduce it."
        )
        explanation = (
            f"{props.get('name', 'This ward')} has {excess} µg/m³ of PM2.5 above its clean-day baseline, "
            f"driven mainly by {SOURCE_LABELS.get(dom, dom)} ({dom_pct}%); "
            f"about {round(local * 100)}% of the excess is locally actionable. {framing}"
        )
    adv_en, adv_hi = _BAND_ADVISORY.get(band, _BAND_ADVISORY["Moderate"])
    return {"explanation": explanation, "advisory_en": adv_en, "advisory_hi": adv_hi, "source": "fallback"}


def _fallback_rationale(entry: dict) -> str:
    dom = entry.get("dominant_local_source") or "local sources"
    return (
        f"Ranked #{entry.get('rank')} — {entry.get('actionable_mass')} µg/m³ of locally-actionable "
        f"{SOURCE_LABELS.get(dom, dom)}; recommended action: {entry.get('action')}."
    )


# ---------------------------------------------------------------------------
# LLM calls (with deterministic fallback on any failure)
# ---------------------------------------------------------------------------
_WARD_SYSTEM = (
    "You explain air-quality source-attribution results that a transparent receptor model has "
    "ALREADY computed. You must NOT compute, change, invent, re-weight, or contradict any number — "
    "use only the figures provided. Be factual, specific, and brief. Reply with ONLY a JSON object "
    "(no markdown, no prose outside it) with exactly these keys: "
    "\"explanation\" (2-3 sentences: what is driving the PM2.5 spike, whether it is locally generated "
    "or advected/regional, and what that implies for action), "
    "\"advisory_en\" (one short health advisory sentence in English), "
    "\"advisory_hi\" (the same advisory in Hindi)."
)


def ward_narration(props: dict, trajectory: Optional[dict] = None) -> dict:
    """Explain one ward's attribution. Returns {explanation, advisory_en, advisory_hi, source}."""
    if not available():
        return _fallback_ward(props)
    shares = props.get("shares", {}) or {}
    facts = {
        "ward": props.get("name"),
        "aqi": props.get("aqi"),
        "aqi_band": props.get("aqi_band"),
        "pm25_ugm3": props.get("pm25"),
        "excess_ugm3_above_clean_day_baseline": props.get("excess"),
        "source_shares_percent": {k: round(v * 100, 1) for k, v in shares.items()},
        "confidence_0_to_1": props.get("confidence"),
        "engine_top_driver": props.get("top_driver_text"),
        "locally_actionable_sources": list(LOCAL_SOURCES),
        "advected_or_regional_sources": ["biomass", "regional"],
    }
    if trajectory:
        facts["upwind_corridor"] = {
            "transport_km": trajectory.get("transport_km"),
            "wind_level": trajectory.get("level"),
            "contributing_fires": trajectory.get("n_contributing_fires"),
            "fires_provenance": trajectory.get("fires_provenance"),
        }
    try:
        resp = _get_client().messages.create(
            model=NARRATION_MODEL,
            max_tokens=NARRATION_MAX_TOKENS,
            system=_WARD_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(facts)}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = _extract_json(text)
        if data and data.get("explanation"):
            return {
                "explanation": str(data["explanation"]),
                "advisory_en": str(data.get("advisory_en") or _BAND_ADVISORY.get(props.get("aqi_band"), ("", ""))[0]),
                "advisory_hi": str(data.get("advisory_hi") or _BAND_ADVISORY.get(props.get("aqi_band"), ("", ""))[1]),
                "source": "llm",
            }
        logger.warning("[narration] ward reply unparseable; using fallback.")
    except Exception as exc:  # noqa: BLE001 - never break the panel
        logger.warning("[narration] ward call failed (%s); using fallback.", exc)
    return _fallback_ward(props)


_ENFORCE_SYSTEM = (
    "You write one-line justifications for an air-quality enforcement queue whose rankings and "
    "figures are ALREADY computed. Do NOT change, invent, or recompute any number or rank — use "
    "only what is given. For each ward, write ONE short sentence explaining why it sits at its rank "
    "for a LOCAL inspector, referencing its dominant local source and recommended action. Reply with "
    "ONLY a JSON object mapping each ward_id to its sentence (no markdown, no other keys)."
)


def enforcement_rationales(queue: list[dict]) -> dict:
    """One-line rationale per queued ward: {ward_id: {rationale, source}}."""
    if not queue:
        return {}
    if not available():
        return {e["ward_id"]: {"rationale": _fallback_rationale(e), "source": "fallback"} for e in queue}
    compact = [
        {
            "ward_id": e.get("ward_id"),
            "rank": e.get("rank"),
            "name": e.get("name"),
            "actionable_mass_ugm3": e.get("actionable_mass"),
            "percent_of_excess_locally_fixable": round((e.get("actionable_frac") or 0) * 100),
            "dominant_local_source": e.get("dominant_local_source"),
            "recommended_action": e.get("action"),
        }
        for e in queue
    ]
    try:
        resp = _get_client().messages.create(
            model=NARRATION_MODEL,
            max_tokens=NARRATION_MAX_TOKENS,
            system=_ENFORCE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(compact)}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = _extract_json(text) or {}
        out = {}
        for e in queue:
            wid = e["ward_id"]
            line = data.get(wid)
            out[wid] = (
                {"rationale": str(line), "source": "llm"}
                if isinstance(line, str) and line.strip()
                else {"rationale": _fallback_rationale(e), "source": "fallback"}
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[narration] enforcement call failed (%s); using fallback.", exc)
        return {e["ward_id"]: {"rationale": _fallback_rationale(e), "source": "fallback"} for e in queue}
