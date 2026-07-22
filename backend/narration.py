"""LLM narration — plain-language explanations of attribution the engine produced.

HARD RULE: the model never computes, changes, invents, or re-weights any number.
It only *explains* figures that are passed in as facts; the prompt says so and we
pass the computed values in.

The provider is swappable via NARRATION_PROVIDER (gemini | groq | anthropic |
bedrock | ollama | none); every call site goes through one `generate()` function,
so no SDK detail leaks out. ANY provider failure — missing key, auth error,
timeout, SDK not installed, "none" — degrades to deterministic text built from the
same figures, so the panel is never empty and never stalls the demo. Set a key and
it flips to live with no code change.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.config import (
    ANTHROPIC_API_KEY,
    AWS_REGION,
    BEDROCK_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    NARRATION_MAX_TOKENS,
    NARRATION_MODEL,
    NARRATION_PROVIDER,
    NARRATION_TIMEOUT_S,
    OLLAMA_MODEL,
    OLLAMA_URL,
)

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
    """Whether a live provider is configured (else callers use the fallback)."""
    p = NARRATION_PROVIDER
    if p == "gemini":
        return bool(GEMINI_API_KEY)
    if p == "groq":
        return bool(GROQ_API_KEY)
    if p == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    if p in ("bedrock", "ollama"):
        return True  # no simple key check; generate() falls back if unreachable
    return False  # "none" or unknown


def provider() -> str:
    return NARRATION_PROVIDER


_clients: dict[str, object] = {}  # lazily-built, cached provider clients


def generate(system: str, prompt: str, max_tokens: int = NARRATION_MAX_TOKENS) -> str:
    """Dispatch one text generation to the configured provider.

    Raises on any failure (callers catch and fall back to deterministic text).
    No provider SDK detail leaks to the call sites — they only ever see a string.
    """
    fn = {
        "gemini": _gen_gemini,
        "groq": _gen_groq,
        "anthropic": _gen_anthropic,
        "bedrock": _gen_bedrock,
        "ollama": _gen_ollama,
    }.get(NARRATION_PROVIDER)
    if fn is None:
        raise RuntimeError(f"narration provider '{NARRATION_PROVIDER}' is not live")
    return fn(system, prompt, max_tokens)


def _gen_gemini(system: str, prompt: str, max_tokens: int) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    from google import genai
    from google.genai import types

    client = _clients.get("gemini")
    if client is None:
        client = genai.Client(api_key=GEMINI_API_KEY)
        _clients["gemini"] = client
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )
    return resp.text or ""


def _gen_groq(system: str, prompt: str, max_tokens: int) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set")
    from openai import OpenAI  # Groq speaks the OpenAI wire protocol

    client = _clients.get("groq")
    if client is None:
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL, timeout=NARRATION_TIMEOUT_S)
        _clients["groq"] = client
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def _gen_anthropic(system: str, prompt: str, max_tokens: int) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic

    client = _clients.get("anthropic")
    if client is None:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        _clients["anthropic"] = client
    resp = client.messages.create(
        model=NARRATION_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


def _gen_bedrock(system: str, prompt: str, max_tokens: int) -> str:
    import boto3  # optional; standard AWS credential chain

    client = _clients.get("bedrock")
    if client is None:
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        _clients["bedrock"] = client
    resp = client.converse(
        modelId=BEDROCK_MODEL,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    return resp["output"]["message"]["content"][0]["text"]


def _gen_ollama(system: str, prompt: str, max_tokens: int) -> str:
    import httpx  # local daemon, no key

    resp = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "format": "json",
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=NARRATION_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "")


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
        text = generate(_WARD_SYSTEM, json.dumps(facts), NARRATION_MAX_TOKENS)
        data = _extract_json(text)
        if data and data.get("explanation"):
            band_en, band_hi = _BAND_ADVISORY.get(props.get("aqi_band"), ("", ""))
            hi = str(data.get("advisory_hi") or "")
            # Safety: if a weak model returns empty/non-Devanagari Hindi, use the
            # curated band advisory rather than show broken script.
            if not re.search(r"[ऀ-ॿ]", hi):
                hi = band_hi
            return {
                "explanation": str(data["explanation"]),
                "advisory_en": str(data.get("advisory_en") or band_en),
                "advisory_hi": hi,
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
        text = generate(_ENFORCE_SYSTEM, json.dumps(compact), NARRATION_MAX_TOKENS)
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
