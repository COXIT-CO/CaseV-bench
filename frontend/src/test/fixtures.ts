import type { ApiMeta } from "@/types";

/** A representative `GET /api/meta` payload shared across tests. */
export const META: ApiMeta = {
  app: "Prompt & Config Lab",
  tasks: ["counting", "location"],
  labels: ["cabinets", "countertops", "elevations", "elevation_callout"],
  drawing_count: 3,
  run_count: 7,
  result_count: 12,
};
