import { SOURCE_ORDER, SOURCE_COLORS, SOURCE_LABELS } from "../lib/palette";
import { pct } from "../lib/format";

// Horizontal 100% stacked bar of the five source shares. Segment order is fixed
// (never sorted by size) so colour always maps to the same source. A 2px surface
// gap separates segments (dataviz mark spec + CVD secondary encoding); shares
// >= 10% are labelled inline, all five are named in the legend beneath.
export default function StackedBar({ shares }) {
  const segs = SOURCE_ORDER.map((s) => ({ s, v: shares?.[s] || 0 })).filter((d) => d.v > 0);

  return (
    <div className="stacked">
      <div className="stacked-track" role="img" aria-label="Source-share breakdown of PM2.5 excess">
        {segs.map(({ s, v }) => (
          <div
            key={s}
            className="stacked-seg"
            style={{ width: `${v * 100}%`, background: SOURCE_COLORS[s] }}
            title={`${SOURCE_LABELS[s]} · ${pct(v)}`}
          >
            {v >= 0.1 && <span className="stacked-lab">{pct(v)}</span>}
          </div>
        ))}
      </div>
      <ul className="stacked-legend">
        {SOURCE_ORDER.map((s) => (
          <li key={s}>
            <span className="swatch" style={{ background: SOURCE_COLORS[s] }} />
            <span className="lg-name">{SOURCE_LABELS[s]}</span>
            <span className="lg-val">{pct(shares?.[s] || 0)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
