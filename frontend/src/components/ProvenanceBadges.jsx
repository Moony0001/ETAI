import { provenanceLabel } from "../lib/format";

// Honesty asset: reads meta.provenance and shows LIVE/DEMO per channel. When the
// OpenAQ + FIRMS keys are present the backend reports "live" and these flip to
// LIVE automatically — no code change here.
export default function ProvenanceBadges({ provenance }) {
  if (!provenance) return null;
  const channels = [
    ["Ground", provenance.ground],
    ["Fires", provenance.fires],
    ["Wind", provenance.wind],
  ];
  return (
    <div className="badges">
      {channels.map(([name, value]) => {
        const live = value === "live";
        return (
          <span key={name} className={`badge ${live ? "live" : "demo"}`} title={`${name}: ${value}`}>
            <i className="dot" />
            {name}: {provenanceLabel(value)}
          </span>
        );
      })}
    </div>
  );
}
