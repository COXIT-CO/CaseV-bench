import type { ApiMeta, LeaderboardResponse } from "@/types";

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
