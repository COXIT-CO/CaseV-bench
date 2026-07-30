// Response types mirroring the FastAPI JSON contract (spec Part A), kept 1:1 with the
// Pydantic response models so the client stays honest about what the API returns. Feature
// types (LeaderboardRow, ResultDetail, …) are added by their own slices.

/** The Tasks the API's contract still names. */
export type Task = "counting" | "location";

/**
 * The only Task this SPA drives (ADR 0032): the benchmark is location-only, and no screen
 * offers a choice. The API is still Task-shaped, so every request that needs a task sends
 * this one; the parameter and the route segment disappear when the contract flattens
 * (ticket 04), taking `Task` with them.
 */
export const SOLE_TASK: Task = "location";

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
 * (src/web/api.py::LeaderboardRowOut), carrying `precision`/`recall`/`f1`. Unscored rows
 * (no ground truth) have `rank: null` and null metrics, already pinned last by the service.
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
  precision: number | null;
  recall: number | null;
  f1: number | null;
}

/** `GET /api/leaderboard` — the ranked board plus the filter surface (spec §A.2). The
 * server echoes the resolved `prompt_family`/`prompt_version` it filtered on (ticket 04). */
export interface LeaderboardResponse {
  task: Task;
  drawing_id: number | null;
  prompt_family: string | null;
  prompt_version: number | null;
  sort: string;
  metrics: string[];
  drawings: LeaderboardDrawing[];
  label_count: number;
  rows: LeaderboardRow[];
}

/** The filter/sort state the Leaderboard mirrors into the URL (spec §B.3). `prompt_version`
 * is only meaningful with a `prompt_family` — the API rejects a version without one (ticket 04). */
export interface LeaderboardParams {
  task: Task;
  drawing_id: number | null;
  prompt_family: string | null;
  prompt_version: number | null;
  sort: string | null;
}

/** One label's location breakdown: the IoU@0.5 tally + its derived rates. */
export interface LocationLabelDetail {
  label: string;
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
}

/** A location Result's Score block: the micro-averaged P/R/F1 headline + per-label rows. */
export interface LocationScore {
  precision: number;
  recall: number;
  f1: number;
  per_label: LocationLabelDetail[];
}

/**
 * One Page's Prediction in the drill-down (src/web/api.py::PredictionOut): the raw model
 * output + parsed JSON on success, or a parse-error failure record. `box_count` is the
 * predicted box count (location only, 0 otherwise).
 */
export interface ResultPrediction {
  page_number: number;
  status: PredictionStatus;
  raw_content: string | null;
  parsed_json: string | null;
  parse_error: string | null;
  box_count: number;
  /** A developer's manual box-JSON override (ADR 0020, ticket 07): `null` unless the boxes
   * were edited. When set, the overlay and JSON view render from it (marked "edited") and
   * `box_count` reflects the edited boxes; scoring ignores it, so the Leaderboard is unmoved. */
  edited_json: string | null;
}

/**
 * `GET /api/results/{id}` — the Result drill-down (spec §A.3, src/web/api.py::ResultDetailResponse).
 * `location_score` is `null` when the Drawing has no ground truth (unscored, distinct from
 * scored-zero).
 */
export interface ResultDetailResponse {
  result_id: number;
  model: string;
  task: Task;
  prompt_family: string;
  prompt_version: number;
  run_id: number;
  drawing_id: number;
  drawing_name: string;
  scored: boolean;
  label_count: number;
  knobs: RunKnobs;
  location_score: LocationScore | null;
  predictions: ResultPrediction[];
}

/** Terminal Run statuses — polling stops and results are viewable at these (spec §B.4). */
export const TERMINAL_RUN_STATUSES: RunStatus[] = ["done", "failed"];

export function isTerminalRunStatus(status: RunStatus | undefined): boolean {
  return status !== undefined && TERMINAL_RUN_STATUSES.includes(status);
}

/** One line of the run history list (`GET /api/runs`, spec §A.4). */
export interface RunListItem {
  id: number;
  task: Task;
  status: RunStatus;
  progress: number;
  total_units: number;
  prompt_family: string;
  prompt_version: number;
  drawing_name: string;
  created_at: string;
}

/** `GET /api/runs` — the run history. */
export interface RunHistoryResponse {
  runs: RunListItem[];
}

/** One selectable prompt version in the launch form; its `task` drives the Run's Task. */
export interface LaunchPrompt {
  id: number;
  task: Task;
  family: string;
  version: number;
}

/** One curated model rendered as a launch-form checkbox/chip. */
export interface CatalogEntry {
  slug: string;
  label: string;
}

/** `GET /api/runs/launch-options` — everything the launch form needs (spec §A.4). */
export interface LaunchOptionsResponse {
  prompts: LaunchPrompt[];
  drawings: LeaderboardDrawing[];
  catalog: CatalogEntry[];
}

/** `POST /api/runs` body: curated slugs + a free-text escape hatch, resolved server-side,
 * plus the Advanced knobs (tickets 04/05). `dpi`/`downsample_px`/`max_tokens` are pre-filled;
 * `temperature` is a number or `null` for the provider default (omitted from the payload), and
 * `downsample_px` is a number or `null` for full resolution (no downsample). */
export interface RunCreateRequest {
  prompt_id: number;
  drawing_id: number;
  models: string[];
  free_text: string;
  dpi: number;
  downsample_px: number | null;
  max_tokens: number;
  temperature: number | null;
}

/** `DELETE /api/runs/{id}` → the collateral the cascade removed (ADR-0016): the Run itself
 * plus its Results — the delete's receipt. (The confirm dialog states the count from the
 * run's own result rows, so it can show the collateral before committing.) */
export interface RunDeleted {
  runs: number;
  results: number;
}

/** `POST /api/runs` → the just-queued Run the SPA routes to (spec §A.4). */
export interface RunCreated {
  id: number;
  status: RunStatus;
  task: Task;
  total_units: number;
}

/** One model's Result row under a Run (links to its Result drill-down once terminal). */
export interface RunResult {
  id: number;
  model: string;
}

/** The run header + live progress on the detail page. */
export interface RunRef {
  id: number;
  task: Task;
  status: RunStatus;
  progress: number;
  total_units: number;
}

/** The prompt reference on a run detail (mirrors the API's `PromptRef`). */
export interface PromptRef {
  family: string;
  version: number;
}

/** The drawing reference on a run detail (mirrors the API's `DrawingRef`). */
export interface DrawingRef {
  id: number;
  name: string;
}

/** The read-only per-run knobs snapshot a Run recorded (spec: Runs 18). `temperature` is null
 * when the Run used the provider default; `downsample_px` is null when the Run sent
 * full-resolution images, no downsample (ADR 0018/0019). */
export interface RunKnobs {
  dpi: number;
  downsample_px: number | null;
  max_tokens: number;
  temperature: number | null;
}

/** `GET /api/runs/{id}` — header + fixed-knobs snapshot + result rows (spec §A.4). */
export interface RunDetailResponse {
  run: RunRef;
  prompt: PromptRef;
  drawing: DrawingRef;
  knobs: RunKnobs;
  results: RunResult[];
}

/** `GET /api/runs/{id}/status` — the cheap poll target that stops at terminal (spec §A.4). */
export interface RunStatusResponse {
  status: RunStatus;
  progress: number;
  total_units: number;
  results: RunResult[];
}

/** One family in the Task-grouped Prompts list (src/web/api.py::PromptFamilyOut): its
 * name, newest version, and version count. */
export interface PromptFamily {
  name: string;
  latest_version: number;
  count: number;
}

/** All of one Task's prompt families (Task-scoping, ADR 0009). */
export interface PromptGroup {
  task: Task;
  families: PromptFamily[];
}

/** `GET /api/prompts` — the fixed Task taxonomy + each Task's families (spec §A.5). */
export interface PromptsResponse {
  tasks: Task[];
  groups: PromptGroup[];
}

/** The prompt families every screen means when it says "the families": the listing is still
 * Task-grouped, so the one group the SPA drives is unwrapped here rather than at each call
 * site. Goes away with the groups themselves when the listing flattens (ticket 04). */
export function promptFamilies(groups: PromptGroup[]): PromptFamily[] {
  return groups.find((group) => group.task === SOLE_TASK)?.families ?? [];
}

/** One immutable version in a family's history; `text` rides along so compare/read needs
 * no extra fetch (src/web/api.py::PromptVersionOut). `run_count`/`result_count` are this
 * version's delete collateral — the Runs + Results that pinned it — so the per-version
 * delete confirm can state the blast radius up front (ADR-0016, ticket 09). */
export interface PromptVersion {
  version: number;
  text: string;
  created_at: string;
  run_count: number;
  result_count: number;
}

/** `GET /api/prompts/{task}/{family}` — the family's versions newest-first (spec §A.5). The
 * family-level `run_count`/`result_count` are the whole-family delete's collateral (the Runs
 * + Results pinning any version), so the "delete family" confirm states the blast radius up
 * front (ADR-0016, ticket 09). */
export interface PromptHistoryResponse {
  task: Task;
  family: string;
  versions: PromptVersion[];
  run_count: number;
  result_count: number;
}

/** `DELETE /api/prompts/{task}/{family}` or `…/versions/{version}` → the collateral the
 * cascade removed (ADR-0016, ticket 09): the Runs + Results that pinned the deleted
 * version(s) — the delete's receipt, the same `(runs, results)` shape the Run and Drawing
 * deletes return. */
export interface PromptDeleted {
  runs: number;
  results: number;
}

/** `POST /api/prompts` body: author a new family's v1 for a Task. */
export interface PromptCreateRequest {
  task: Task;
  family: string;
  text: string;
}

/** The just-written version returned from create/append, so the SPA routes to it. */
export interface PromptVersionRef {
  task: Task;
  family: string;
  version: number;
}

/** One Drawing in the Library list / created-upload response (src/web/api.py::DrawingSummary):
 * its id, name, and page count. Same shape as `LeaderboardDrawing`, named for the Library. */
export interface DrawingSummary {
  id: number;
  name: string;
  page_count: number;
}

/** `GET /api/drawings` — the Library catalog of Drawings, newest-first (spec §A.6). */
export interface DrawingsResponse {
  drawings: DrawingSummary[];
}

/** One rendered Page on the Drawing detail (src/web/api.py::DrawingPageOut): its number, the
 * full-resolution pixel dims, and the URL of its cached image PNG (under `/api`). */
export interface DrawingPage {
  page_number: number;
  width_px: number;
  height_px: number;
  image_url: string;
}

/** `GET /api/drawings/{id}` — the Drawing plus its rendered Pages (spec §A.6). The detail is
 * where ground-truth entry hangs off (ticket 07) and where the Drawing can be deleted
 * (ticket 08); `run_count`/`result_count` are the delete's collateral — the Runs + Results
 * that used this Drawing — so the confirm dialog states the blast radius up front (ADR-0016). */
export interface DrawingDetailResponse {
  drawing: DrawingRef;
  pages: DrawingPage[];
  run_count: number;
  result_count: number;
}

/** `DELETE /api/drawings/{id}` → the collateral the cascade removed (ADR-0016): the Runs and
 * Results that used the Drawing — the delete's receipt, the same `(runs, results)` shape the
 * Run delete returns. */
export interface DrawingDeleted {
  runs: number;
  results: number;
}

/** `GET /api/models` — the user-editable model catalog (spec §A.6, ticket 10). Selection
 * stays inline at Run launch; this endpoint views and edits the catalog (ADR 0011 amended by
 * ticket 10). */
export interface ModelsResponse {
  catalog: CatalogEntry[];
}

/** `POST /api/models` body: add a model (slug + label) to the catalog; re-posting a known
 * slug re-labels it (upsert, ticket 10). The response is the stored `CatalogEntry`. */
export interface ModelUpsertRequest {
  slug: string;
  label: string;
}

/** One object the native import reported rather than silently dropped: an off-taxonomy
 * category, a reference to a page the Drawing lacks, a box that grossly overflows its page's
 * native frame, or one enclosing no area — zero width/height or inverted coordinates
 * (src/api/routers/ground_truth.py::ImportProblemOut). */
export interface LocationImportProblem {
  kind: "unmapped_label" | "unknown_page" | "out_of_frame" | "degenerate_box";
  detail: string;
}

/** `POST /api/drawings/{id}/location-ground-truth` → the native import result: how many boxes
 * were created and every problem reported (spec §A.6). */
export interface LocationImportResponse {
  created: number;
  problems: LocationImportProblem[];
}
