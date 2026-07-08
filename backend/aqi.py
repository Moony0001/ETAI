"""Official Indian National AQI (CPCB) from a PM2.5 concentration.

CPCB publishes sub-index breakpoints per pollutant; this module implements the
PM2.5 (24-hour) sub-index, which is the driving pollutant for Delhi's winter
smog. The sub-index is a piecewise-linear interpolation between breakpoints:

    AQI = (I_hi - I_lo) / (BP_hi - BP_lo) * (C - BP_lo) + I_lo

Bands (CPCB): Good / Satisfactory / Moderate / Poor / Very Poor / Severe.

Reference: CPCB "National Air Quality Index" (2014), PM2.5 24-hr breakpoints.
"""

from __future__ import annotations

from typing import NamedTuple

# (conc_lo, conc_hi, aqi_lo, aqi_hi, band) — PM2.5 µg/m³, 24-hr sub-index.
_PM25_BREAKPOINTS: tuple[tuple[float, float, float, float, str], ...] = (
    (0.0, 30.0, 0, 50, "Good"),
    (30.0, 60.0, 51, 100, "Satisfactory"),
    (60.0, 90.0, 101, 200, "Moderate"),
    (90.0, 120.0, 201, 300, "Poor"),
    (120.0, 250.0, 301, 400, "Very Poor"),
    (250.0, 380.0, 401, 500, "Severe"),
)

# Canonical band order + the CPCB legend colours (reused by the frontend legend).
AQI_BANDS: tuple[str, ...] = (
    "Good",
    "Satisfactory",
    "Moderate",
    "Poor",
    "Very Poor",
    "Severe",
)
AQI_BAND_COLORS: dict[str, str] = {
    "Good": "#55a84f",
    "Satisfactory": "#a3c853",
    "Moderate": "#fff833",
    "Poor": "#f29c33",
    "Very Poor": "#e93f33",
    "Severe": "#af2d24",
}


class AQIResult(NamedTuple):
    aqi: int
    band: str


def pm25_to_aqi(pm25: float) -> AQIResult:
    """Map a PM2.5 concentration (µg/m³) to (AQI, band) via CPCB breakpoints.

    Values above the top breakpoint saturate at AQI 500 / "Severe" (CPCB caps
    the reported index at 500).
    """
    if pm25 is None or pm25 < 0:
        return AQIResult(0, "Good")
    for conc_lo, conc_hi, aqi_lo, aqi_hi, band in _PM25_BREAKPOINTS:
        if pm25 <= conc_hi:
            aqi = (aqi_hi - aqi_lo) / (conc_hi - conc_lo) * (pm25 - conc_lo) + aqi_lo
            return AQIResult(int(round(aqi)), band)
    return AQIResult(500, "Severe")
