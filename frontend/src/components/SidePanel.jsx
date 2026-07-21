import { useEffect, useState } from "react";
import StackedBar from "./StackedBar";
import { AQI_BAND_COLORS } from "../lib/palette";
import { confidenceBand } from "../lib/format";
import { getNarration } from "../lib/api";

// The differentiator: one clean attribution panel for the selected ward. Every
// number here comes straight from the batch feature's properties; the narration
// (explanation + EN/HI advisory) is fetched async and never blocks the panel.
export default function SidePanel({ props, date, corridorState, onShowCorridor, onClearCorridor }) {
  const [narration, setNarration] = useState(null);
  const [advisoryLang, setAdvisoryLang] = useState("en");

  useEffect(() => {
    if (!props?.ward_id) return;
    let cancelled = false;
    setNarration({ loading: true });
    getNarration(props.ward_id, date)
      .then((n) => !cancelled && setNarration(n))
      .catch(() => !cancelled && setNarration(null)); // silent: deterministic driver text remains
    return () => {
      cancelled = true;
    };
  }, [props?.ward_id, date]);

  if (!props) {
    return (
      <aside className="panel empty">
        <div className="panel-hint">
          <div className="hint-mark">◎</div>
          Click any ward to attribute its PM2.5 excess to sources — then trace the
          upwind corridor that carried the smoke in.
        </div>
      </aside>
    );
  }

  const bandColor = AQI_BAND_COLORS[props.aqi_band] || "#8b98a5";

  return (
    <aside className="panel">
      <div className="panel-head">
        <div className="pname">{props.name}</div>
        <div className="ward-id">{props.ward_id}</div>
      </div>

      <div className="aqi-row">
        <div className="aqi-val" style={{ color: bandColor }}>
          {props.aqi}
        </div>
        <div className="aqi-meta">
          <span className="band-chip" style={{ background: bandColor }}>
            {props.aqi_band}
          </span>
          <span className="aqi-sub">PM2.5 {props.pm25} µg/m³</span>
        </div>
      </div>

      <div className="excess-row">
        <span>Excess above clean-day baseline</span>
        <b>
          +{props.excess} <em>µg/m³</em>
        </b>
      </div>

      <div className="section-label">Attributed excess by source</div>
      <StackedBar shares={props.shares} />

      <div className="driver">{props.top_driver_text}</div>

      {narration?.loading && <div className="narration muted">Generating explanation…</div>}
      {narration?.explanation && (
        <div className="narration">
          <p className="narration-text">{narration.explanation}</p>
          <div className="advisory">
            <div className="advisory-head">
              <span>Health advisory</span>
              <div className="lang-toggle">
                <button className={advisoryLang === "en" ? "on" : ""} onClick={() => setAdvisoryLang("en")}>
                  EN
                </button>
                <button className={advisoryLang === "hi" ? "on" : ""} onClick={() => setAdvisoryLang("hi")}>
                  हिं
                </button>
              </div>
            </div>
            <p className="advisory-text">
              {advisoryLang === "en" ? narration.advisory_en : narration.advisory_hi}
            </p>
          </div>
          {narration.source === "fallback" && (
            <div className="narration-note">
              deterministic summary · set ANTHROPIC_API_KEY for AI narration
            </div>
          )}
        </div>
      )}

      <div className="conf-row">
        <span>Confidence</span>
        <b>
          {confidenceBand(props.confidence)}
          <span className="conf-num"> · {props.confidence}</span>
        </b>
      </div>

      <div className="corridor-actions">
        {corridorState.active ? (
          <button className="btn ghost" onClick={onClearCorridor}>
            Hide corridor
          </button>
        ) : (
          <button className="btn primary" onClick={onShowCorridor} disabled={corridorState.loading}>
            {corridorState.loading ? "Tracing…" : "Show source corridor →"}
          </button>
        )}
      </div>
      {corridorState.info && <div className="corridor-info">{corridorState.info}</div>}
    </aside>
  );
}
