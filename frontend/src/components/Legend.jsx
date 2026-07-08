import {
  SOURCE_ORDER,
  SOURCE_COLORS,
  SOURCE_LABELS,
  AQI_BANDS,
  AQI_BAND_COLORS,
} from "../lib/palette";

export default function Legend({ mode }) {
  const items =
    mode === "aqi"
      ? AQI_BANDS.map((b) => ({ key: b, color: AQI_BAND_COLORS[b], label: b }))
      : SOURCE_ORDER.map((s) => ({ key: s, color: SOURCE_COLORS[s], label: SOURCE_LABELS[s] }));

  return (
    <div className="legend">
      <div className="legend-title">{mode === "aqi" ? "AQI band · CPCB" : "Dominant source"}</div>
      <ul>
        {items.map((it) => (
          <li key={it.key}>
            <span className="swatch" style={{ background: it.color }} />
            {it.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
