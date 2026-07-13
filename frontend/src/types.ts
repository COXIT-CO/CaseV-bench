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

/** One entry in the Leaderboard's Drawing filter dropdown (spec §A.2). */
export interface LeaderboardDrawing {
  id: number;
  name: string;
  page_count: number;
}

/**
 * One Leaderboard line: a prompt-version × model Configuration outcome
 * (src/web/api.py::LeaderboardRowOut). Counting rows carry `total_absolute_error` +
 * `exact_match_count`; location rows carry `precision`/`recall`/`f1`; the unused set is
 * `null`. Unscored rows (no ground truth) have `rank: null` and null metrics, already
 * pinned last by the service.
 */
export interface LeaderboardRow {
  rank: number | null;
  result_id: number;
  run_id: number;
  model: string;
  prompt_family: string;
  prompt_version: number;
  drawing_id: number;
  drawing_name: string;
  scored: boolean;
  total_absolute_error: number | null;
  exact_match_count: number | null;
  precision: number | null;
  recall: number | null;
  f1: number | null;
}

/** `GET /api/leaderboard` — the ranked board plus the filter surface (spec §A.2). */
export interface LeaderboardResponse {
  task: Task;
  drawing_id: number | null;
  sort: string;
  metrics: string[];
  drawings: LeaderboardDrawing[];
  label_count: number;
  rows: LeaderboardRow[];
}

/** The filter/sort state the Leaderboard mirrors into the URL (spec §B.3). */
export interface LeaderboardParams {
  task: Task;
  drawing_id: number | null;
  sort: string | null;
}
