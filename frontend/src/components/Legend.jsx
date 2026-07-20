import {
  SOURCE_ORDER,
  SOURCE_COLORS,
  SOURCE_LABELS,
  AQI_BANDS,
  AQI_BAND_COLORS,
  ACTION_RAMP,
  ACTION_MAX_UGM3,
} from "../lib/palette";

const rgb = (a) => `rgb(${a[0]},${a[1]},${a[2]})`;

export default function Legend({ mode }) {
  if (mode === "action") {
    return (
      <div className="legend">
        <div className="legend-title">Actionable pollution</div>
        <div
          className="legend-gradient"
          style={{ background: `linear-gradient(90deg, ${rgb(ACTION_RAMP.lo)}, ${rgb(ACTION_RAMP.hi)})` }}
        />
        <div className="legend-scale">
          <span>0</span>
          <span>local µg/m³</span>
          <span>≥{ACTION_MAX_UGM3}</span>
        </div>
      </div>
    );
  }

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
