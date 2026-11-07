// Object categories, in the order of their 1–5 shortcut keys. `prefix` builds the saved ids
// (`cab-001`, …) and `color` is the box outline; both must stay in sync with the dataset.
export const CATEGORIES = [
  { name: "cabinet", prefix: "cab", color: "#2563eb" },
  { name: "countertop", prefix: "ctp", color: "#d97706" },
  { name: "elevation", prefix: "elv", color: "#16a34a" },
  { name: "callout", prefix: "cal", color: "#db2777" },
  { name: "floor plan", prefix: "flp", color: "#7c3aed" },
];

export const DEFAULT_CATEGORY = CATEGORIES[0].name;

// Categories that are not in the list above (e.g. from a hand-edited JSON) are kept as is.
const UNKNOWN = { prefix: "obj", color: "#64748b" };

export function categoryInfo(name) {
  return CATEGORIES.find((c) => c.name === name) ?? { name, ...UNKNOWN };
}
