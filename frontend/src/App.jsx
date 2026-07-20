import { useCallback, useEffect, useRef, useState } from "react";
import { WebMercatorViewport, FlyToInterpolator } from "@deck.gl/core";
import MapView from "./components/MapView";
import SidePanel from "./components/SidePanel";
import Legend from "./components/Legend";
import Header from "./components/Header";
import { getAttribution, getTrajectory } from "./lib/api";

const INITIAL_VIEW = { longitude: 77.15, latitude: 28.62, zoom: 9.1, pitch: 0, bearing: 0 };
const DEFAULT_DATE = "2024-11-08"; // opens on the pre-cached stubble episode

const NO_CORRIDOR = { loading: false, active: false, info: null };

export default function App() {
  const [date, setDate] = useState(DEFAULT_DATE);
  const [colorMode, setColorMode] = useState("source");
  const [meta, setMeta] = useState(null);
  const [geojson, setGeojson] = useState(null);
  const [selected, setSelected] = useState(null); // selected feature's properties
  const [trajectory, setTrajectory] = useState(null);
  const [corridor, setCorridor] = useState(NO_CORRIDOR);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [viewState, setViewState] = useState(INITIAL_VIEW);
  const wrapRef = useRef(null);

  // Refetch the whole-city snapshot whenever the date changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setTrajectory(null);
    setCorridor(NO_CORRIDOR);
    getAttribution(date)
      .then((res) => {
        if (cancelled) return;
        setMeta(res.meta);
        setGeojson(res.geojson);
        // keep the selected ward across a date change by re-reading its new props
        setSelected((prev) => {
          if (!prev) return prev;
          const f = res.geojson.features.find((x) => x.properties.ward_id === prev.ward_id);
          return f ? f.properties : prev;
        });
      })
      .catch((e) => !cancelled && setError(String(e.message || e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [date]);

  const onWardClick = useCallback((props) => {
    setSelected(props);
    setTrajectory(null);
    setCorridor(NO_CORRIDOR);
  }, []);

  const fitTo = useCallback((coords) => {
    const el = wrapRef.current;
    const width = el?.clientWidth || 900;
    const height = el?.clientHeight || 600;
    let minLng = Infinity;
    let minLat = Infinity;
    let maxLng = -Infinity;
    let maxLat = -Infinity;
    coords.forEach(([lng, lat]) => {
      minLng = Math.min(minLng, lng);
      minLat = Math.min(minLat, lat);
      maxLng = Math.max(maxLng, lng);
      maxLat = Math.max(maxLat, lat);
    });
    if (!isFinite(minLng)) return;
    try {
      const vp = new WebMercatorViewport({ width, height });
      const { longitude, latitude, zoom } = vp.fitBounds(
        [
          [minLng, minLat],
          [maxLng, maxLat],
        ],
        { padding: 90 }
      );
      setViewState((v) => ({
        ...v,
        longitude,
        latitude,
        zoom: Math.min(zoom, 10.5),
        transitionDuration: 1200,
        transitionInterpolator: new FlyToInterpolator({ speed: 1.4 }),
      }));
    } catch (_) {
      /* fitBounds can throw on degenerate bounds; keep current view */
    }
  }, []);

  const onShowCorridor = useCallback(() => {
    if (!selected) return;
    setCorridor({ loading: true, active: false, info: null });
    getTrajectory(selected.ward_id, date)
      .then((t) => {
        setTrajectory(t.geojson);
        const coords = [];
        t.geojson.features.forEach((f) => {
          if (f.geometry.type === "LineString") f.geometry.coordinates.forEach((c) => coords.push(c));
          else if (f.geometry.type === "Point") coords.push(f.geometry.coordinates);
        });
        if (coords.length) fitTo(coords);
        const n = t.n_contributing_fires;
        const prov = t.fires_provenance === "archive" ? " · archive fires" : "";
        setCorridor({
          loading: false,
          active: true,
          info: `${t.transport_km} km on ${t.level} wind · ${n} contributing fire${n === 1 ? "" : "s"}${prov}`,
        });
      })
      .catch((e) => setCorridor({ loading: false, active: false, info: `corridor error: ${e.message || e}` }));
  }, [selected, date, fitTo]);

  const onClearCorridor = useCallback(() => {
    setTrajectory(null);
    setCorridor(NO_CORRIDOR);
  }, []);

  return (
    <div className="app">
      <Header
        date={date}
        onDateChange={setDate}
        colorMode={colorMode}
        onColorModeChange={setColorMode}
        meta={meta}
        loading={loading}
      />
      <div className="stage">
        <div className="map-wrap" ref={wrapRef}>
          <MapView
            geojson={geojson}
            trajectory={trajectory}
            colorMode={colorMode}
            viewState={viewState}
            onViewStateChange={setViewState}
            onWardClick={onWardClick}
            selectedWardId={selected?.ward_id}
          />
          <Legend mode={colorMode} />
          {meta && (
            <div className="metastrip">
              {meta.city} · {meta.date} · {meta.station_count} stations · {meta.fire_count} fires ·{" "}
              {meta.ward_count} cells{meta.cached ? " · cached" : ""}
            </div>
          )}
          {loading && <div className="overlay">Computing city-wide attribution…</div>}
          {error && (
            <div className="overlay error">
              API error: {error}
              <br />
              <small>Is the backend running on :8000?</small>
            </div>
          )}
        </div>
        <SidePanel
          props={selected}
          corridorState={corridor}
          onShowCorridor={onShowCorridor}
          onClearCorridor={onClearCorridor}
        />
      </div>
    </div>
  );
}
