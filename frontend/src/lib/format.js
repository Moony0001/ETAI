import { SOURCE_ORDER } from "./palette";

/** "#rrggbb" -> [r, g, b] for deck.gl colour accessors. */
export function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

/** argmax of the 5 source shares (canonical order breaks ties); null if no excess. */
export function dominantSource(shares) {
  if (!shares) return null;
  let best = null;
  let bestVal = 0;
  for (const s of SOURCE_ORDER) {
    const v = shares[s] || 0;
    if (v > bestVal) {
      bestVal = v;
      best = s;
    }
  }
  return best;
}

/** Map a 0..1 confidence to a coarse band (0.43 -> "Low"). */
export function confidenceBand(c) {
  if (c == null) return "—";
  if (c < 0.5) return "Low";
  if (c < 0.7) return "Med";
  return "High";
}

/** Provenance value -> badge label. "archive" is honest real data, not DEMO. */
export function provenanceLabel(value) {
  if (value === "live") return "LIVE";
  if (value === "archive") return "ARCHIVE";
  if (value === "synthetic_fallback") return "DEMO";
  return "—";
}

/** Provenance value -> badge style class. */
export function provenanceClass(value) {
  if (value === "live") return "live";
  if (value === "archive") return "archive";
  if (value === "synthetic_fallback") return "demo";
  return "none";
}

export function pct(x) {
  return `${Math.round((x || 0) * 100)}%`;
}
