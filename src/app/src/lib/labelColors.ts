// Fixed per-ObjectType overlay colours (ADR 0021, ticket 06). These MUST mirror the backend
// palette in `core/utils.py::LABEL_COLORS` so the overlay PNG the API renders and the legend
// the SPA draws agree on which colour means which class. Okabe-Ito qualitative palette: each
// reads on a white drawing and the four stay distinguishable under colour-vision deficiencies.

/** The taxonomy labels paired with their overlay colour, in canonical order — the source for
 * the prediction-overlay legend's per-label colour key. */
export const LABEL_COLORS: ReadonlyArray<{ label: string; color: string }> = [
  { label: "cabinets", color: "#D55E00" },
  { label: "countertops", color: "#0072B2" },
  { label: "elevations", color: "#009E73" },
  { label: "elevation_callout", color: "#CC79A7" },
];
