import { SOURCE_COLORS, SOURCE_LABELS } from "../lib/palette";

// The intervention view: wards ranked by pollution a local inspector can actually
// act on (traffic/dust/industrial mass), with a recommended action — plus the
// contrast list of wards whose burden is advected stubble smoke (coordinate
// regionally, don't deploy an inspector). Lives on the LEFT so it never fights
// the attribution panel on the right.
export default function EnforcementPanel({ data, loading, error, selectedWardId, onSelectWard, onClose }) {
  return (
    <aside className="enforce-panel">
      <div className="enforce-head">
        <div>
          <div className="enforce-title">Enforcement queue</div>
          <div className="enforce-sub">Ranked by locally-actionable PM2.5 · not raw AQI</div>
        </div>
        <button className="enforce-close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      {loading && <div className="enforce-empty">Ranking wards…</div>}
      {error && <div className="enforce-empty err">{error}</div>}

      {data && (
        <>
          <ol className="enforce-queue">
            {data.queue.map((e) => {
              const src = e.dominant_local_source;
              return (
                <li
                  key={e.ward_id}
                  className={`enforce-item ${e.ward_id === selectedWardId ? "sel" : ""}`}
                  onClick={() => onSelectWard(e)}
                >
                  <div className="rank">{e.rank}</div>
                  <div className="ebody">
                    <div className="erow1">
                      <span className="ename">{e.name}</span>
                      <span className="emass">
                        {e.actionable_mass} <em>µg/m³</em>
                      </span>
                    </div>
                    <div className="erow2">
                      <span className="src-chip" style={{ background: SOURCE_COLORS[src] || "#8b98a5" }}>
                        {SOURCE_LABELS[src] || "local"}
                      </span>
                      <span className="eaction">{e.action}</span>
                    </div>
                    <div className="erow3">
                      {Math.round((e.actionable_frac || 0) * 100)}% of excess is locally fixable · AQI {e.aqi}
                    </div>
                  </div>
                </li>
              );
            })}
            {data.queue.length === 0 && <div className="enforce-empty">No locally-actionable wards.</div>}
          </ol>

          {data.regional?.length > 0 && (
            <div className="enforce-regional">
              <div className="enforce-regional-title">Not locally actionable — regional coordination</div>
              <div className="enforce-regional-sub">
                Highest-excess wards driven by advected stubble smoke / regional background.
              </div>
              <ul>
                {data.regional.map((r) => (
                  <li key={r.ward_id} onClick={() => onSelectWard(r)}>
                    <span className="reg-dot" style={{ background: SOURCE_COLORS[r.dominant_source] || "#8b98a5" }} />
                    <span className="reg-name">{r.name}</span>
                    <span className="reg-ex">
                      {r.excess} µg/m³ · {Math.round(r.regional_share * 100)}% {r.dominant_source}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </aside>
  );
}
