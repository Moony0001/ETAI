import React, { useEffect, useMemo, useState } from "react";
import DeckGL from "@deck.gl/react";
import { GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers";
import { Map } from "react-map-gl/maplibre";

// Keyless MapLibre style (no Mapbox token).
const MAP_STYLE = "https://demotiles.maplibre.org/style.json";
const API = "/api"; // Vite proxies /api -> http://127.0.0.1:8000

const SOURCE_COLORS = {
  biomass: "#e8590c",
  traffic: "#1c7ed6",
  dust: "#f2c94c",
  industrial: "#9c36b5",
  regional: "#868e96",
};

async function getJSON(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

export default function App() {
  const [attr, setAttr] = useState(null);
  const [traj, setTraj] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const a = await getJSON("/attribution");
        setAttr(a);
        const t = await getJSON(`/trajectory/${a.ward_id}`);
        setTraj(t.geojson);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, []);

  const initialView = {
    longitude: attr?.lon ?? 77.209,
    latitude: attr?.lat ?? 28.6139,
    zoom: 8.2,
    pitch: 0,
  };

  const layers = useMemo(() => {
    if (!traj) return [];
    return [
      new GeoJsonLayer({
        id: "trajectory",
        data: traj,
        getLineColor: [90, 200, 250],
        getLineWidth: 3,
        lineWidthUnits: "pixels",
        pointRadiusUnits: "pixels",
        getPointRadius: (f) => (f.properties.kind === "fire" ? 5 : 2),
        getFillColor: (f) =>
          f.properties.kind === "fire" ? [232, 89, 12, 220] : [90, 200, 250, 160],
        stroked: false,
        pickable: true,
      }),
      attr &&
        new ScatterplotLayer({
          id: "ward",
          data: [attr],
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 6,
          radiusUnits: "pixels",
          getFillColor: [46, 204, 113, 230],
        }),
    ].filter(Boolean);
  }, [traj, attr]);

  return (
    <div className="app">
      <div className="panel">
        <h1>vayulens</h1>
        <div className="sub">Delhi air-quality source attribution</div>

        {error && <div className="err">API error: {error}<br />Is the backend running on :8000?</div>}
        {!attr && !error && <div className="sub">Loading attribution…</div>}

        {attr && (
          <>
            <span className="badge">data: {attr.data_source}</span>
            <div className="stat"><span>Ward</span><b>{attr.ward_name}</b></div>
            <div className="stat"><span>PM2.5 observed</span><b>{attr.pm25_obs} µg/m³</b></div>
            <div className="stat"><span>Baseline (10th pct)</span><b>{attr.pm25_baseline} µg/m³</b></div>
            <div className="stat"><span>Excess</span><b>{attr.excess} µg/m³</b></div>
            <div className="stat"><span>Confidence</span><b>{(attr.confidence * 100).toFixed(0)}%</b></div>

            <div className="section">
              <b style={{ fontSize: 13 }}>Attributed excess by source</b>
              {Object.entries(attr.shares)
                .sort((a, b) => b[1] - a[1])
                .map(([src, share]) => (
                  <div className="share-row" key={src}>
                    <div className="share-head">
                      <span style={{ color: SOURCE_COLORS[src] }}>{src}</span>
                      <span>{(share * 100).toFixed(0)}% · {attr.masses[src]} µg/m³</span>
                    </div>
                    <div className="bar">
                      <span style={{ width: `${share * 100}%`, background: SOURCE_COLORS[src] }} />
                    </div>
                  </div>
                ))}
            </div>

            <div className="drivers section">
              <b style={{ fontSize: 13, color: "var(--ink)" }}>Top drivers</b>
              <ul>{attr.top_drivers.map((d, i) => <li key={i}>{d}</li>)}</ul>
              {attr.notes?.length > 0 && (
                <>
                  <b style={{ fontSize: 12 }}>Notes</b>
                  <ul>{attr.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                </>
              )}
            </div>
          </>
        )}
      </div>

      <div className="map-wrap">
        <DeckGL initialViewState={initialView} controller={true} layers={layers}>
          <Map mapStyle={MAP_STYLE} reuseMaps />
        </DeckGL>
      </div>
    </div>
  );
}
