import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { LaunchRunDialog } from "@/components/LaunchRun";
import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useLeaderboard } from "@/hooks/queries";
import {
  UNSCORED_CELL,
  formatExactMatch,
  formatRate,
  metricColumns,
  metricLabel,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type { LeaderboardRow, Task } from "@/types";

// The Landing page (ADR 0011, spec §A.2/§B.3): every Result ranked best-first for the
// chosen Task, filtered by Drawing and ranked by a metric — all mirrored into the URL so
// a board link is shareable. The service ranks; this screen renders, it does not re-rank.

const TASKS: { value: Task; label: string }[] = [
  { value: "counting", label: "Counting" },
  { value: "location", label: "Location" },
];

/** The `?task=` value, defaulting to `counting` for anything but `location` (spec §A.2). */
function parseTask(raw: string | null): Task {
  return raw === "location" ? "location" : "counting";
}

/** The `?drawing_id=` value as a number, or `null` for "All drawings"/garbage. */
function parseDrawingId(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const id = Number(raw);
  return Number.isInteger(id) ? id : null;
}

export function Leaderboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const task = parseTask(searchParams.get("task"));
  const drawingId = parseDrawingId(searchParams.get("drawing_id"));
  const sort = searchParams.get("sort");

  const { data, isLoading, isError, error } = useLeaderboard({
    task,
    drawing_id: drawingId,
    sort,
  });

  // Reflect a filter change into the URL query so the board is shareable and back/forward
  // work (spec §B.3). `null`/"all" drops the param so the server applies its default.
  function patchParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setSearchParams(next);
  }

  // Switching Task swaps both the ranking metrics and the score columns; the old Task's
  // `sort` is meaningless here, so drop it and let the new Task's default apply.
  function selectTask(next: Task) {
    if (next !== task) patchParams({ task: next, sort: null });
  }

  const columns = metricColumns(task);
  // The rank-by options + resolved sort come from the server (it already fell back an
  // unknown metric to the Task default), so the dropdown always mirrors the live board.
  const metrics = data?.metrics ?? [];
  const activeSort = data?.sort ?? "";

  return (
    <section className="mx-auto max-w-[1400px]">
      <header className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Leaderboard</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Compare prompt + model configurations by score.
          </p>
        </div>
        <LaunchRunDialog />
      </header>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label="Task"
          className="inline-flex items-center gap-0.5 rounded-lg border bg-card p-0.5"
        >
          {TASKS.map((t) => (
            <button
              key={t.value}
              role="tab"
              aria-selected={task === t.value}
              onClick={() => selectTask(t.value)}
              className={cn(
                "rounded-md px-3.5 py-1.5 text-[13px] font-semibold transition-colors",
                task === t.value
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label
            htmlFor="lb-drawing"
            className="text-xs font-medium text-muted-foreground"
          >
            Drawing
          </label>
          <select
            id="lb-drawing"
            value={drawingId ?? "all"}
            onChange={(e) =>
              patchParams({
                drawing_id: e.target.value === "all" ? null : e.target.value,
              })
            }
            className="rounded-md border bg-card px-2.5 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="all">All drawings</option>
            {data?.drawings.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>

          <label
            htmlFor="lb-sort"
            className="ml-1 text-xs font-medium text-muted-foreground"
          >
            Rank by
          </label>
          <select
            id="lb-sort"
            value={activeSort}
            onChange={(e) => patchParams({ sort: e.target.value })}
            disabled={metrics.length === 0}
            className="rounded-md border bg-card px-2.5 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {metrics.map((m) => (
              <option key={m} value={m}>
                {metricLabel(m)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {isLoading ? (
        <LoadingBlock rows={8} />
      ) : isError ? (
        <ErrorBlock error={error} />
      ) : data && data.rows.length === 0 ? (
        <EmptyState
          title="No results yet — launch a run"
          description="Launch a run to see scored configurations here."
          action={<LaunchRunDialog />}
        />
      ) : (
        data && (
          <div className="overflow-x-auto rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-14">Rank</TableHead>
                  <TableHead>Prompt</TableHead>
                  <TableHead>Model</TableHead>
                  {columns.map((col) => (
                    <TableHead key={col.key} className="text-right">
                      {col.header}
                    </TableHead>
                  ))}
                  <TableHead>Drawing</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.rows.map((row) => (
                  <BoardRow
                    key={row.result_id}
                    row={row}
                    columns={columns}
                    labelCount={data.label_count}
                    onOpen={() => navigate(`/results/${row.result_id}`)}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )
      )}
    </section>
  );
}

/** The metric cell text for one column, or `—` when the row is unscored (spec §C.2). */
function metricText(
  row: LeaderboardRow,
  key: string,
  labelCount: number,
): string {
  if (!row.scored) return UNSCORED_CELL;
  switch (key) {
    case "total_absolute_error":
      return String(row.total_absolute_error);
    case "exact_match_count":
      return formatExactMatch(row.exact_match_count ?? 0, labelCount);
    case "precision":
      return formatRate(row.precision ?? 0);
    case "recall":
      return formatRate(row.recall ?? 0);
    case "f1":
      return formatRate(row.f1 ?? 0);
    default:
      return UNSCORED_CELL;
  }
}

function BoardRow({
  row,
  columns,
  labelCount,
  onOpen,
}: {
  row: LeaderboardRow;
  columns: ReturnType<typeof metricColumns>;
  labelCount: number;
  onOpen: () => void;
}) {
  const isLeader = row.rank === 1;
  return (
    <TableRow
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`Open result for ${row.model}`}
      className={cn(
        "cursor-pointer",
        !row.scored && "text-muted-foreground opacity-60",
      )}
    >
      <TableCell
        className={cn(
          "font-semibold",
          isLeader ? "text-success" : !row.scored && "text-muted-foreground",
        )}
      >
        {row.scored ? `#${row.rank}` : UNSCORED_CELL}
      </TableCell>
      <TableCell>
        <div className="font-medium text-foreground">{row.prompt_family}</div>
        <div className="font-mono text-[11.5px] text-muted-foreground">
          v{row.prompt_version}
        </div>
      </TableCell>
      <TableCell className="font-mono text-xs">{row.model}</TableCell>
      {columns.map((col) => (
        <TableCell
          key={col.key}
          className={cn(
            "text-right font-mono text-[12.5px]",
            isLeader && col.emphasis
              ? "font-bold text-success"
              : row.scored && "text-foreground",
          )}
        >
          {metricText(row, col.key, labelCount)}
        </TableCell>
      ))}
      <TableCell className="text-[12.5px] text-muted-foreground">
        <span className="inline-flex items-center gap-2">
          {row.drawing_name}
          {!row.scored && (
            <Link
              to={`/library/drawings/${row.drawing_id}#ground-truth`}
              onClick={(e) => e.stopPropagation()}
              className="font-semibold text-primary hover:underline"
            >
              + ground truth
            </Link>
          )}
        </span>
      </TableCell>
    </TableRow>
  );
}
