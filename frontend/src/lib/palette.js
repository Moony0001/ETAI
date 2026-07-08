// One fixed source-colour palette, reused across the map fill, the legend and
// the attribution bar. Validated for colour-vision-deficiency separation against
// the dark command-centre surface (dataviz skill validator, dark mode):
//   worst all-pairs ΔE 10.1 (tritan) — legal in the floor band because every
//   surface that uses it also carries a text label or a 2px gap (secondary
//   encoding). Order is fixed and never cycled.

export const SOURCE_ORDER = ["biomass", "traffic", "dust", "industrial", "regional"];

export const SOURCE_COLORS = {
  biomass: "#d9622e", // orange — stubble/smoke
  traffic: "#3987e5", // blue
  dust: "#c98500", // amber — mineral/earth
  industrial: "#d55181", // magenta — stacks
  regional: "#8b98a5", // neutral grey — shared common-mode (an intentional neutral)
};

export const SOURCE_LABELS = {
  biomass: "Biomass burning",
  traffic: "Traffic",
  dust: "Dust",
  industrial: "Industrial",
  regional: "Regional",
};

// Official CPCB National-AQI band colours (used by the AQI-severity view + legend).
export const AQI_BANDS = ["Good", "Satisfactory", "Moderate", "Poor", "Very Poor", "Severe"];

export const AQI_BAND_COLORS = {
  Good: "#55a84f",
  Satisfactory: "#a3c853",
  Moderate: "#fff833",
  Poor: "#f29c33",
  "Very Poor": "#e93f33",
  Severe: "#af2d24",
};
