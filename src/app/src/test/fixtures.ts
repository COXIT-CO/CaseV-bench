import type {
  ApiMeta,
  CountingGroundTruthResponse,
  DrawingDetailResponse,
  DrawingsResponse,
  LaunchOptionsResponse,
  LeaderboardResponse,
  LocationImportResponse,
  ModelsResponse,
  PromptHistoryResponse,
  PromptsResponse,
  ResultDetailResponse,
  RunDetailResponse,
  RunHistoryResponse,
  RunStatusResponse,
} from "@/types";

/** A representative `GET /api/meta` payload shared across tests. */
export const META: ApiMeta = {
  app: "Prompt & Config Lab",
  tasks: ["counting", "location"],
  labels: ["cabinets", "countertops", "elevations", "elevation_callout"],
  drawing_count: 3,
  run_count: 7,
  result_count: 12,
};

const DRAWINGS = [
  { id: 3, name: "prj0001", page_count: 4 },
  { id: 5, name: "prj0002", page_count: 2 },
];

/** A counting board with two scored rows (a rank-1 leader) and one unscored row. */
export const COUNTING_BOARD: LeaderboardResponse = {
  task: "counting",
  drawing_id: null,
  sort: "total_absolute_error",
  metrics: ["total_absolute_error", "exact_match_count"],
  drawings: DRAWINGS,
  label_count: 4,
  rows: [
    {
      rank: 1,
      result_id: 42,
      run_id: 7,
      model: "anthropic/claude-sonnet-4.5",
      prompt_family: "count-v2",
      prompt_version: 3,
      drawing_id: 3,
      drawing_name: "prj0001",
      scored: true,
      total_absolute_error: 0,
      exact_match_count: 4,
      precision: null,
      recall: null,
      f1: null,
    },
    {
      rank: 2,
      result_id: 43,
      run_id: 7,
      model: "openai/gpt-5-mini",
      prompt_family: "count-v2",
      prompt_version: 3,
      drawing_id: 3,
      drawing_name: "prj0001",
      scored: true,
      total_absolute_error: 6,
      exact_match_count: 3,
      precision: null,
      recall: null,
      f1: null,
    },
    {
      rank: null,
      result_id: 44,
      run_id: 8,
      model: "google/gemini-2.5-pro",
      prompt_family: "count-v1",
      prompt_version: 1,
      drawing_id: 5,
      drawing_name: "prj0002",
      scored: false,
      total_absolute_error: null,
      exact_match_count: null,
      precision: null,
      recall: null,
      f1: null,
    },
  ],
};

/** A location board (P/R/F1 columns) with a single scored row. */
export const LOCATION_BOARD: LeaderboardResponse = {
  task: "location",
  drawing_id: null,
  sort: "f1",
  metrics: ["f1", "precision", "recall"],
  drawings: DRAWINGS,
  label_count: 4,
  rows: [
    {
      rank: 1,
      result_id: 90,
      run_id: 12,
      model: "anthropic/claude-sonnet-4.5",
      prompt_family: "loc-v1",
      prompt_version: 2,
      drawing_id: 3,
      drawing_name: "prj0001",
      scored: true,
      total_absolute_error: null,
      exact_match_count: null,
      precision: 0.8,
      recall: 0.67,
      f1: 0.73,
    },
  ],
};

/** An empty board — no Results yet. */
export const EMPTY_BOARD: LeaderboardResponse = {
  task: "counting",
  drawing_id: null,
  sort: "total_absolute_error",
  metrics: ["total_absolute_error", "exact_match_count"],
  drawings: [],
  label_count: 4,
  rows: [],
};

/** A scored counting Result: an exact page + a parse-failure page. */
export const COUNTING_RESULT: ResultDetailResponse = {
  result_id: 42,
  model: "anthropic/claude-sonnet-4.5",
  task: "counting",
  prompt_family: "strict-json",
  prompt_version: 9,
  run_id: 812,
  drawing_id: 3,
  drawing_name: "prj0001",
  scored: true,
  label_count: 4,
  counting_score: {
    total_absolute_error: 3,
    exact_match_count: 3,
    per_label: [
      { label: "cabinets", predicted: 24, gt: 22, absolute_error: 2, exact_match: false },
      { label: "countertops", predicted: 8, gt: 8, absolute_error: 0, exact_match: true },
      { label: "elevations", predicted: 7, gt: 7, absolute_error: 0, exact_match: true },
      { label: "elevation_callout", predicted: 12, gt: 12, absolute_error: 0, exact_match: true },
    ],
  },
  location_score: null,
  predictions: [
    {
      page_number: 1,
      status: "ok",
      // Raw is the model's verbatim (minified) reply; parsed is the normalized JSON the
      // UI pretty-prints — so the two blocks render distinguishably.
      raw_content: '{"cabinets":24,"countertops":8,"elevations":7,"elevation_callout":12}',
      parsed_json: '{"cabinets": 24, "countertops": 8, "elevations": 7, "elevation_callout": 12}',
      parse_error: null,
      box_count: 0,
    },
    {
      page_number: 2,
      status: "error",
      raw_content: "not json",
      parsed_json: null,
      parse_error: "response was not valid JSON",
      box_count: 0,
    },
  ],
};

/** A scored location Result with two predicted pages. */
export const LOCATION_RESULT: ResultDetailResponse = {
  result_id: 90,
  model: "anthropic/claude-sonnet-4.5",
  task: "location",
  prompt_family: "boxes",
  prompt_version: 2,
  run_id: 12,
  drawing_id: 3,
  drawing_name: "floorplan",
  scored: true,
  label_count: 4,
  counting_score: null,
  location_score: {
    precision: 0.87,
    recall: 0.81,
    f1: 0.84,
    per_label: [
      { label: "cabinets", tp: 22, fp: 3, fn: 2, precision: 0.88, recall: 0.92, f1: 0.9 },
      { label: "countertops", tp: 14, fp: 2, fn: 4, precision: 0.88, recall: 0.78, f1: 0.82 },
      { label: "elevations", tp: 9, fp: 1, fn: 1, precision: 0.9, recall: 0.9, f1: 0.9 },
      { label: "elevation_callout", tp: 11, fp: 4, fn: 2, precision: 0.73, recall: 0.85, f1: 0.79 },
    ],
  },
  predictions: [
    {
      page_number: 1,
      status: "ok",
      raw_content: '{"detections": []}',
      parsed_json: '{"detections": []}',
      parse_error: null,
      box_count: 11,
    },
    {
      page_number: 2,
      status: "ok",
      raw_content: '{"detections": []}',
      parsed_json: '{"detections": []}',
      parse_error: null,
      box_count: 6,
    },
  ],
};

/** An unscored Result (no ground truth for its Drawing). */
export const UNSCORED_RESULT: ResultDetailResponse = {
  ...COUNTING_RESULT,
  result_id: 43,
  scored: false,
  counting_score: null,
  location_score: null,
};

/** An unscored location Result: no ground truth yet, but the model's predicted boxes are
 * still inspectable via the prediction-only overlay (ticket 12). */
export const UNSCORED_LOCATION_RESULT: ResultDetailResponse = {
  ...LOCATION_RESULT,
  result_id: 44,
  scored: false,
  location_score: null,
  predictions: [
    {
      page_number: 1,
      status: "ok",
      raw_content: '{"detections": []}',
      parsed_json: '{"detections": []}',
      parse_error: null,
      box_count: 5,
    },
  ],
};

/** A run history with one running and one done Run. */
export const RUN_HISTORY: RunHistoryResponse = {
  runs: [
    {
      id: 812,
      task: "location",
      status: "running",
      progress: 3,
      total_units: 6,
      prompt_family: "strict-json",
      prompt_version: 9,
      drawing_name: "prj0001",
      created_at: "2026-07-13T10:00:00Z",
    },
    {
      id: 811,
      task: "counting",
      status: "done",
      progress: 6,
      total_units: 6,
      prompt_family: "cabinet-count-v2",
      prompt_version: 12,
      drawing_name: "prj0002",
      created_at: "2026-07-13T09:00:00Z",
    },
  ],
};

/** The launch form's option set: two prompts, two drawings, three curated models. */
export const LAUNCH_OPTIONS: LaunchOptionsResponse = {
  prompts: [
    { id: 9, task: "counting", family: "count-v2", version: 3 },
    { id: 4, task: "location", family: "loc-v1", version: 2 },
  ],
  drawings: [
    { id: 3, name: "prj0001", page_count: 4 },
    { id: 5, name: "prj0002", page_count: 2 },
  ],
  catalog: [
    { slug: "anthropic/claude-sonnet-4.5", label: "Claude Sonnet 4.5" },
    { slug: "openai/gpt-5-mini", label: "GPT-5 mini" },
    { slug: "google/gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  ],
};

/** An empty launch option set — no prompts and no drawings yet. */
export const EMPTY_LAUNCH_OPTIONS: LaunchOptionsResponse = {
  prompts: [],
  drawings: [],
  catalog: [],
};

/** A run detail whose knobs snapshot and result rows the detail page renders. */
export const RUN_DETAIL: RunDetailResponse = {
  run: { id: 812, task: "location", status: "running", progress: 3, total_units: 6 },
  prompt: { family: "strict-json", version: 9 },
  drawing: { id: 3, name: "prj0001" },
  knobs: {
    dpi: 200,
    downsample_px: 1600,
    max_tokens: 4096,
    temperature: 0.0,
  },
  results: [
    { id: 42, model: "anthropic/claude-sonnet-4.5" },
    { id: 43, model: "openai/gpt-5-mini" },
  ],
};

/** The Prompts list: counting has two families, location has the seeded default. */
export const PROMPTS: PromptsResponse = {
  tasks: ["counting", "location"],
  groups: [
    {
      task: "counting",
      families: [
        { name: "cabinet-count-v2", latest_version: 3, count: 3 },
        { name: "default", latest_version: 1, count: 1 },
      ],
    },
    {
      task: "location",
      families: [{ name: "default", latest_version: 1, count: 1 }],
    },
  ],
};

/** A counting family's history with two immutable versions, newest-first. The delete
 * collateral (ADR-0016): v2 is pinned by 2 runs / 5 results, v1 by 1 run / 3 results, so the
 * family total is 3 runs / 8 results. */
export const PROMPT_HISTORY: PromptHistoryResponse = {
  task: "counting",
  family: "cabinet-count-v2",
  run_count: 3,
  result_count: 8,
  versions: [
    {
      version: 2,
      text: "Count only base cabinets, ignore wall cabinets.",
      created_at: "2026-06-18T12:00:00Z",
      run_count: 2,
      result_count: 5,
    },
    {
      version: 1,
      text: "Count every cabinet in the drawing.",
      created_at: "2026-06-17T09:00:00Z",
      run_count: 1,
      result_count: 3,
    },
  ],
};

/** A single-version family — compare falls back to reading v1 against itself. No runs pin it,
 * so deleting it (its sole version) removes an empty-collateral family. */
export const PROMPT_HISTORY_SINGLE: PromptHistoryResponse = {
  task: "location",
  family: "default",
  run_count: 0,
  result_count: 0,
  versions: [
    {
      version: 1,
      text: "Return bounding boxes for every fixture.",
      created_at: "2026-06-10T09:00:00Z",
      run_count: 0,
      result_count: 0,
    },
  ],
};

/** The Library Drawings list with two ingested drawings. */
export const DRAWINGS_LIST: DrawingsResponse = {
  drawings: [
    { id: 3, name: "prj0001", page_count: 4 },
    { id: 5, name: "prj0002", page_count: 1 },
  ],
};

/** One Drawing's detail: two rendered pages with distinct pixel dimensions, plus the
 * delete-collateral counts (2 runs / 5 results used this Drawing). */
export const DRAWING_DETAIL: DrawingDetailResponse = {
  drawing: { id: 3, name: "prj0001" },
  run_count: 2,
  result_count: 5,
  pages: [
    {
      page_number: 1,
      width_px: 1700,
      height_px: 2200,
      image_url: "/api/drawings/3/pages/1/image",
    },
    {
      page_number: 2,
      width_px: 2200,
      height_px: 1700,
      image_url: "/api/drawings/3/pages/2/image",
    },
  ],
};

/** A counting-GT pre-fill with existing totals (cabinets already entered, one label still
 * unentered/null) so a form seeds mixed state. */
export const COUNTING_GT: CountingGroundTruthResponse = {
  drawing_id: 3,
  labels: [
    { name: "cabinets", value: 4 },
    { name: "countertops", value: 2 },
    { name: "elevations", value: 1 },
    { name: "elevation_callout", value: null },
  ],
};

/** A counting-GT pre-fill with nothing entered yet — every label null. */
export const COUNTING_GT_EMPTY: CountingGroundTruthResponse = {
  drawing_id: 3,
  labels: [
    { name: "cabinets", value: null },
    { name: "countertops", value: null },
    { name: "elevations", value: null },
    { name: "elevation_callout", value: null },
  ],
};

/** A COCO import result: one box created, plus one unmapped label and one unknown page
 * reported rather than silently dropped. */
export const LOCATION_IMPORT_RESULT: LocationImportResponse = {
  created: 37,
  problems: [
    { kind: "unmapped_label", detail: "no taxonomy mapping for label 'windows'" },
    {
      kind: "unknown_page",
      detail: "annotation references page 9, which drawing 3 does not have",
    },
  ],
};

/** The curated model catalog for the Library Models view. */
export const MODELS: ModelsResponse = {
  catalog: [
    { slug: "anthropic/claude-sonnet-4.5", label: "Claude Sonnet 4.5" },
    { slug: "openai/gpt-5-mini", label: "GPT-5 mini" },
    { slug: "google/gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  ],
};

/** A terminal (done) status payload with results ready to view. */
export const RUN_STATUS_DONE: RunStatusResponse = {
  status: "done",
  progress: 6,
  total_units: 6,
  results: [
    { id: 42, model: "anthropic/claude-sonnet-4.5" },
    { id: 43, model: "openai/gpt-5-mini" },
  ],
};
