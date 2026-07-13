import type {
  ApiMeta,
  LeaderboardResponse,
  ResultDetailResponse,
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
      has_gt: false,
      box_count: 0,
    },
    {
      page_number: 2,
      status: "error",
      raw_content: "not json",
      parsed_json: null,
      parse_error: "response was not valid JSON",
      has_gt: false,
      box_count: 0,
    },
  ],
};

/** A scored location Result: page 1 has GT, page 2 does not. */
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
      has_gt: true,
      box_count: 11,
    },
    {
      page_number: 2,
      status: "ok",
      raw_content: '{"detections": []}',
      parsed_json: '{"detections": []}',
      parse_error: null,
      has_gt: false,
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
