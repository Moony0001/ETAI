import ProvenanceBadges from "./ProvenanceBadges";

const TODAY = new Date().toISOString().slice(0, 10);
const PRESETS = [
  { label: "Nov 8 · episode", date: "2024-11-08" },
  { label: "Nov 18 · NW corridor", date: "2024-11-18" },
];

export default function Header({ date, onDateChange, colorMode, onColorModeChange, meta, loading }) {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="mark">vāyulens</div>
        <div className="tag">Delhi · PM2.5 source attribution</div>
      </div>

      <div className="controls">
        <input
          className="date-input"
          type="date"
          value={date}
          max={TODAY}
          onChange={(e) => e.target.value && onDateChange(e.target.value)}
        />
        {PRESETS.map((p) => (
          <button
            key={p.date}
            className={`chip ${date === p.date ? "on" : ""}`}
            onClick={() => onDateChange(p.date)}
          >
            {p.label}
          </button>
        ))}
        <button className={`chip ${date === TODAY ? "on" : ""}`} onClick={() => onDateChange(TODAY)}>
          Today
        </button>
        {loading && <span className="live-compute">computing…</span>}
      </div>

      <div className="topright">
        <div className="toggle" role="tablist" aria-label="Colour mode">
          <button className={colorMode === "source" ? "on" : ""} onClick={() => onColorModeChange("source")}>
            Source
          </button>
          <button className={colorMode === "aqi" ? "on" : ""} onClick={() => onColorModeChange("aqi")}>
            AQI
          </button>
        </div>
        <ProvenanceBadges provenance={meta?.provenance} />
      </div>
    </header>
  );
}
