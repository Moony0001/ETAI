import { useMemo } from "react";
import DeckGL from "@deck.gl/react";
import { GeoJsonLayer, ScatterplotLayer, PathLayer } from "@deck.gl/layers";
import { Map } from "react-map-gl/maplibre";
import { SOURCE_COLORS, AQI_BAND_COLORS } from "../lib/palette";
import { hexToRgb, dominantSource, actionableMass, actionColor } from "../lib/format";

// Keyless dark basemap: the MapLibre demotiles vector source, restyled dark so
// the choropleth reads as a command-centre overlay. No API key, no token.
const DARK_STYLE = {
  version: 8,
  sources: {
    demotiles: { type: "vector", url: "https://demotiles.maplibre.org/tiles/tiles.json" },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0a0e13" } },
    {
      id: "countries",
      type: "fill",
      source: "demotiles",
      "source-layer": "countries",
      paint: { "fill-color": "#111821", "fill-outline-color": "#20303f" },
    },
  ],
};

const MUTED = [74, 86, 100]; // wards with no attributable excess render muted, not blank

function fillFor(props, colorMode) {
  if (colorMode === "aqi") {
    const c = AQI_BAND_COLORS[props.aqi_band];
    return c ? hexToRgb(c) : MUTED;
  }
  if (colorMode === "action") {
    if (!props.excess || props.excess <= 0) return MUTED;
    return actionColor(actionableMass(props));
  }
  if (!props.excess || props.excess <= 0) return MUTED;
  const dom = dominantSource(props.shares);
  return dom ? hexToRgb(SOURCE_COLORS[dom]) : MUTED;
}

function getTooltip({ object }) {
  const p = object?.properties;
  if (!p) return null;
  if (p.kind === "fire") return { text: `Active fire · FRP ${Math.round(p.frp)} MW · ${p.dist_km} km from path` };
  if (p.name) return { text: `${p.name}\nAQI ${p.aqi} · ${p.aqi_band}\nPM2.5 ${p.pm25} µg/m³` };
  return null;
}

export default function MapView({
  geojson,
  trajectory,
  colorMode,
  viewState,
  onViewStateChange,
  onWardClick,
  selectedWardId,
}) {
  const layers = useMemo(() => {
    const ls = [];

    if (geojson) {
      ls.push(
        new GeoJsonLayer({
          id: "wards",
          data: geojson,
          pickable: true,
          stroked: true,
          filled: true,
          getFillColor: (f) => {
            const sel = f.properties.ward_id === selectedWardId;
            return [...fillFor(f.properties, colorMode), sel ? 245 : 165];
          },
          getLineColor: (f) =>
            f.properties.ward_id === selectedWardId ? [255, 255, 255, 255] : [255, 255, 255, 20],
          getLineWidth: (f) => (f.properties.ward_id === selectedWardId ? 2 : 0.4),
          lineWidthUnits: "pixels",
          onClick: (info) => info.object && onWardClick(info.object.properties),
          updateTriggers: {
            getFillColor: [colorMode, selectedWardId],
            getLineColor: [selectedWardId],
            getLineWidth: [selectedWardId],
          },
        })
      );
    }

    if (trajectory) {
      const line = trajectory.features.find((f) => f.properties.kind === "trajectory");
      const nodes = trajectory.features.filter((f) => f.properties.kind === "node");
      const fires = trajectory.features.filter((f) => f.properties.kind === "fire");

      if (line) {
        ls.push(
          new PathLayer({
            id: "corridor-path",
            data: [{ path: line.geometry.coordinates }],
            getPath: (d) => d.path,
            getColor: [125, 211, 252, 235],
            getWidth: 4,
            widthUnits: "pixels",
            capRounded: true,
            jointRounded: true,
            parameters: { depthTest: false },
          })
        );
      }
      ls.push(
        new ScatterplotLayer({
          id: "corridor-nodes",
          data: nodes,
          getPosition: (f) => f.geometry.coordinates,
          getRadius: 3,
          radiusUnits: "pixels",
          getFillColor: [186, 230, 253, 210],
          stroked: true,
          getLineColor: [10, 14, 19, 255],
          lineWidthMinPixels: 1,
        })
      );
      ls.push(
        new ScatterplotLayer({
          id: "corridor-fires",
          data: fires,
          pickable: true,
          getPosition: (f) => f.geometry.coordinates,
          // radius grows with Fire Radiative Power (warm colour); kept small so a
          // dense stubble field (~1000+ detections) reads as a belt, not one blob.
          getRadius: (f) => Math.sqrt(f.properties.frp || 1) * 1.8,
          radiusUnits: "pixels",
          radiusMinPixels: 2,
          radiusMaxPixels: 14,
          getFillColor: [255, 130, 40, 200],
          stroked: fires.length < 120,
          getLineColor: [255, 90, 0, 255],
          lineWidthMinPixels: 1,
        })
      );
    }

    return ls;
  }, [geojson, trajectory, colorMode, selectedWardId, onWardClick]);

  return (
    <DeckGL
      viewState={viewState}
      controller={{ dragRotate: false }}
      onViewStateChange={(e) => onViewStateChange(e.viewState)}
      layers={layers}
      getTooltip={getTooltip}
    >
      <Map mapStyle={DARK_STYLE} reuseMaps attributionControl={false} />
    </DeckGL>
  );
}
