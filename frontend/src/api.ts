import type {
  ApiMeta,
  CountingGroundTruthResponse,
  CountingGtSaveRequest,
  DrawingDetailResponse,
  DrawingsResponse,
  DrawingSummary,
  LaunchOptionsResponse,
  LeaderboardParams,
  LeaderboardResponse,
  LocationImportResponse,
  ModelsResponse,
  PromptCreateRequest,
  PromptHistoryResponse,
  PromptsResponse,
  PromptVersionRef,
  ResultDetailResponse,
  RunCreated,
  RunCreateRequest,
  RunDetailResponse,
  RunHistoryResponse,
  RunStatusResponse,
} from "@/types";

// Thin typed fetch client over `/api/**` (spec §B.1). In dev, Vite proxies these paths to
// FastAPI, so relative URLs need no origin and no CORS. Every method mirrors one Part-A
// endpoint; feature calls are added per slice.

/** Error carrying the API's `{"detail": …}` envelope so the SPA can surface it (spec §A.0). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" } });
  } catch {
    // Network / proxy failure — no HTTP status to read.
    throw new ApiError(0, "Could not reach the API. Is the FastAPI server running?");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  return (await response.json()) as T;
}

/** POST a JSON body and parse the JSON response, surfacing the same `{detail}` envelope
 * as `getJson` (spec §A.0). Used by the mutating endpoints (e.g. launching a Run). */
async function postJson<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Could not reach the API. Is the FastAPI server running?");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  return (await response.json()) as T;
}

/** PUT a JSON body and parse the JSON response, surfacing the same `{detail}` envelope as
 * `postJson` (spec §A.0). Used by the idempotent upserts (e.g. saving counting ground
 * truth). */
async function putJson<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "PUT",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Could not reach the API. Is the FastAPI server running?");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  return (await response.json()) as T;
}

/** POST `multipart/form-data` (a file upload) and parse the JSON response, surfacing the
 * same `{detail}` envelope as the other helpers (spec §A.6). The browser sets the multipart
 * `Content-Type` (with its boundary) from the `FormData`, so it must not be set here. */
async function postForm<T>(path: string, form: FormData): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { Accept: "application/json" },
      body: form,
    });
  } catch {
    throw new ApiError(0, "Could not reach the API. Is the FastAPI server running?");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  return (await response.json()) as T;
}

/** Pull FastAPI's `{"detail": …}` message, falling back to the status text. */
async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Non-JSON error body; fall through.
  }
  return response.statusText || `Request failed (${response.status})`;
}

export const api = {
  /** App-level facts for the shell (proves the Vite → JSON → shadcn pipeline). */
  meta: () => getJson<ApiMeta>("/api/meta"),

  /**
   * The ranked Leaderboard for a Task, filtered by Drawing and ranked by a metric
   * (spec §A.2). Params map 1:1 to the URL query the SPA mirrors; `null` values are
   * omitted so the server applies its defaults ("All drawings", the task's default sort).
   */
  leaderboard: ({ task, drawing_id, sort }: LeaderboardParams) => {
    const query = new URLSearchParams({ task });
    if (drawing_id !== null) query.set("drawing_id", String(drawing_id));
    if (sort !== null) query.set("sort", sort);
    return getJson<LeaderboardResponse>(`/api/leaderboard?${query}`);
  },

  /** One Result's drill-down: header refs, the score block, and the per-page predictions (spec §A.3). */
  result: (id: number) => getJson<ResultDetailResponse>(`/api/results/${id}`),

  /** The run history, newest-first (spec §A.4). */
  runs: () => getJson<RunHistoryResponse>("/api/runs"),

  /** The launch form's option set: prompt versions, drawings, curated catalog (spec §A.4). */
  launchOptions: () =>
    getJson<LaunchOptionsResponse>("/api/runs/launch-options"),

  /** Launch a Run; the server resolves the slug list and returns the queued Run (spec §A.4). */
  createRun: (body: RunCreateRequest) =>
    postJson<RunCreated>("/api/runs", body),

  /** One Run's detail: header, fixed-knobs snapshot, and result rows (spec §A.4). */
  run: (id: number) => getJson<RunDetailResponse>(`/api/runs/${id}`),

  /** The poll target: live progress + results once terminal (spec §A.4/§B.4). */
  runStatus: (id: number) =>
    getJson<RunStatusResponse>(`/api/runs/${id}/status`),

  /** Prompt families grouped by Task, each with its latest version + count (spec §A.5). */
  prompts: () => getJson<PromptsResponse>("/api/prompts"),

  /** Author a new family's v1; a duplicate family surfaces the service `400` (spec §A.5). */
  createPrompt: (body: PromptCreateRequest) =>
    postJson<PromptVersionRef>("/api/prompts", body),

  /** A family's immutable version history, newest-first, each version's text included so
   * compare/read needs no follow-up fetch (spec §A.5). */
  promptHistory: (task: string, family: string) =>
    getJson<PromptHistoryResponse>(
      `/api/prompts/${encodeURIComponent(task)}/${encodeURIComponent(family)}`,
    ),

  /** "Edit" = append the next immutable version to a family (ADR 0009, spec §A.5). */
  appendPromptVersion: (task: string, family: string, text: string) =>
    postJson<PromptVersionRef>(
      `/api/prompts/${encodeURIComponent(task)}/${encodeURIComponent(family)}/versions`,
      { text },
    ),

  /** The Library catalog of Drawings with page counts, newest-first (spec §A.6). */
  drawings: () => getJson<DrawingsResponse>("/api/drawings"),

  /** Upload a PDF (multipart); ingests via `DrawingService` and returns the created
   * Drawing so the SPA can route to its detail (spec §A.6). */
  uploadDrawing: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return postForm<DrawingSummary>("/api/drawings", form);
  },

  /** One Drawing's rendered Pages with pixel dims + image URLs (spec §A.6). */
  drawing: (id: number) =>
    getJson<DrawingDetailResponse>(`/api/drawings/${id}`),

  /** The curated model catalog for the Library view (spec §A.6). */
  models: () => getJson<ModelsResponse>("/api/models"),

  /** Pre-fill: one Drawing's counting-GT totals per taxonomy label (spec §A.6). */
  countingGroundTruth: (id: number) =>
    getJson<CountingGroundTruthResponse>(
      `/api/drawings/${id}/counting-ground-truth`,
    ),

  /** Upsert one Drawing's counting-GT totals; returns the saved totals (spec §A.6). */
  saveCountingGroundTruth: (id: number, totals: CountingGtSaveRequest) =>
    putJson<CountingGroundTruthResponse>(
      `/api/drawings/${id}/counting-ground-truth`,
      totals,
    ),

  /** Import a Drawing's LocationGroundTruth from a COCO JSON upload (multipart), surfacing
   * the importer's problem report (spec §A.6). An optional `label_map` (a JSON object) maps
   * external category names onto the taxonomy. */
  importLocationGroundTruth: (id: number, file: File, labelMap?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (labelMap) form.append("label_map", labelMap);
    return postForm<LocationImportResponse>(
      `/api/drawings/${id}/location-ground-truth`,
      form,
    );
  },
};
