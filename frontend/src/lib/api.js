// API base comes from a Vite env var; defaults to the dev proxy (/api -> :8000)
// so there is never a CORS error in local development.
const API_BASE = (import.meta.env.VITE_API_URL || "/api").replace(/\/+$/, "");

async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail || "";
    } catch (_) {
      /* ignore */
    }
    throw new Error(`${path} -> ${res.status}${detail ? ` (${detail})` : ""}`);
  }
  return res.json();
}

/** Whole-city attribution snapshot: { meta, geojson }. */
export function getAttribution(date) {
  const q = date ? `?date=${encodeURIComponent(date)}` : "";
  return getJSON(`/attribution${q}`);
}

/** Back-trajectory corridor for a ward centroid on a date. */
export function getTrajectory(wardId, date) {
  const p = new URLSearchParams();
  if (wardId) p.set("ward_id", wardId);
  if (date) p.set("date", date);
  return getJSON(`/trajectory?${p.toString()}`);
}

/** Enforcement queue: wards ranked by locally-actionable pollution. */
export function getEnforcement(date, limit = 20) {
  const p = new URLSearchParams();
  if (date) p.set("date", date);
  p.set("limit", String(limit));
  return getJSON(`/enforcement?${p.toString()}`);
}

export { API_BASE };
