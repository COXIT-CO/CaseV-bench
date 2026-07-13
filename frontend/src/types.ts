// Response types mirroring the FastAPI JSON contract (spec Part A), kept 1:1 with the
// Pydantic response models so the client stays honest about what the API returns. Feature
// types (LeaderboardRow, ResultDetail, …) are added by their own slices.

/** The two Tasks the harness scores. */
export type Task = "counting" | "location";

/** Run lifecycle status. */
export type RunStatus = "queued" | "running" | "done" | "failed";

/** Per-page prediction status. */
export type PredictionStatus = "ok" | "error";

/** `GET /api/meta` — the slice-0 proof payload (src/web/api.py::ApiMeta). */
export interface ApiMeta {
  app: string;
  tasks: Task[];
  labels: string[];
  drawing_count: number;
  run_count: number;
  result_count: number;
}
