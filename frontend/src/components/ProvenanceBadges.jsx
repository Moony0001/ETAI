import { provenanceLabel, provenanceClass } from "../lib/format";

// Honesty asset: reads meta.provenance and shows LIVE / ARCHIVE / DEMO per
// channel. "archive" = FIRMS science-quality historical fires (real data, not a
// demo). When keys are present the backend reports live/archive and these flip
// automatically — no code change here.
export default function ProvenanceBadges({ provenance, windLevel }) {
  if (!provenance) return null;
  const channels = [
    ["Ground", provenance.ground],
    ["Fires", provenance.fires],
    ["Wind", provenance.wind],
  ];
  return (
    <div className="badges">
      {channels.map(([name, value]) => (
        <span
          key={name}
          className={`badge ${provenanceClass(value)}`}
          title={name === "Wind" && windLevel ? `Wind: ${value} · ${windLevel}` : `${name}: ${value}`}
        >
          <i className="dot" />
          {name}: {provenanceLabel(value)}
          {name === "Wind" && windLevel ? ` · ${windLevel}` : ""}
        </span>
      ))}
    </div>
  );
}
