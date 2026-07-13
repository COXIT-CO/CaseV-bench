import type { Task } from "@/types";

// Metric labels, column definitions, and cell formatting for the board (spec §B.1). The
// API returns metric *slugs*; these map them to the human labels the mockup shows and
// keep the per-Task column set in one place so Leaderboard just renders it.

/** Human label for a `sort` metric slug (the "Rank by" dropdown + column headers). */
const METRIC_LABELS: Record<string, string> = {
  total_absolute_error: "Total abs. error",
  exact_match_count: "Exact matches",
  f1: "F1",
  precision: "Precision",
  recall: "Recall",
};

export function metricLabel(slug: string): string {
  return METRIC_LABELS[slug] ?? slug;
}

/** The `—` shown for an unscored row's metric cells (spec §C.2, MetricCell). */
export const UNSCORED_CELL = "—";

/** Location rates render to two decimals; `0.7` reads as `0.70` so columns line up. */
export function formatRate(value: number): string {
  return value.toFixed(2);
}

/** An ISO timestamp as a short local date (e.g. a prompt version's authored day). Falls
 * back to the raw string if it isn't a parseable date. */
export function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
}

/** Exact matches read as `n / total` against the taxonomy size (spec §A.2). */
export function formatExactMatch(count: number, labelCount: number): string {
  return `${count} / ${labelCount}`;
}

/**
 * The fixed score columns for a Task (spec §A.2): counting shows total absolute error +
 * exact matches; location shows Precision · Recall · F1. Each column knows its header and
 * how to pull + format its value from a row. `emphasis` marks the ranked-on-by-default
 * leader column that gets the rank-1 win accent (brief §2).
 */
export interface MetricColumn {
  key: string;
  header: string;
  /** Whether this column carries the win/leader emphasis for the rank-1 row. */
  emphasis: boolean;
}

const COUNTING_COLUMNS: MetricColumn[] = [
  { key: "total_absolute_error", header: "Total abs. error", emphasis: true },
  { key: "exact_match_count", header: "Exact matches", emphasis: false },
];

const LOCATION_COLUMNS: MetricColumn[] = [
  { key: "precision", header: "Precision", emphasis: false },
  { key: "recall", header: "Recall", emphasis: false },
  { key: "f1", header: "F1", emphasis: true },
];

export function metricColumns(task: Task): MetricColumn[] {
  return task === "location" ? LOCATION_COLUMNS : COUNTING_COLUMNS;
}
