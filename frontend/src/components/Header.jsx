import ProvenanceBadges from "./ProvenanceBadges";

const TODAY = new Date().toISOString().slice(0, 10);
const PRESETS = [
  { label: "Nov 8 · peak episode", date: "2024-11-08" },
  { label: "Nov 18 · corridor", date: "2024-11-18" },
];

const COLOR_MODES = [
  ["source", "Source"],
  ["aqi", "AQI"],
  ["action", "Action"],
];

export default function Header({
  date,
  onDateChange,
  colorMode,
  onColorModeChange,
  meta,
  loading,
  enforcementOpen,
  onToggleEnforcement,
}) {
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
        <button
          className={`enforce-toggle ${enforcementOpen ? "on" : ""}`}
          onClick={onToggleEnforcement}
          aria-pressed={enforcementOpen}
        >
          ⚑ Enforcement
        </button>
        <div className="toggle" role="tablist" aria-label="Colour mode">
          {COLOR_MODES.map(([key, label]) => (
            <button key={key} className={colorMode === key ? "on" : ""} onClick={() => onColorModeChange(key)}>
              {label}
            </button>
          ))}
        </div>
        <ProvenanceBadges provenance={meta?.provenance} windLevel={meta?.wind_level} />
      </div>
    </header>
  );
}
