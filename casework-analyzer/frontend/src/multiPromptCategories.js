// Shared between App.jsx (state + request orchestration) and
// PromptEditor.jsx (the 4-field UI) for Multi-Prompting mode. `key` matches
// config.categories / ExportCountResponse's field names (plural) and is
// exactly what's sent as AnalyzeRequest.category; `label` is just the UI's
// display text ("Callouts" rather than the full "elevation_callouts").
export const MULTI_PROMPT_CATEGORIES = [
  { key: "cabinets", label: "Cabinets" },
  { key: "elevations", label: "Elevations" },
  { key: "countertops", label: "Countertops" },
  { key: "elevation_callouts", label: "Callouts" },
];
