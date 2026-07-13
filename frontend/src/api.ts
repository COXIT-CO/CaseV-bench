import type {
  ApiMeta,
  LeaderboardParams,
  LeaderboardResponse,
  ResultDetailResponse,
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
};
