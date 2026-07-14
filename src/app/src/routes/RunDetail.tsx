import { Link, useNavigate, useParams } from "react-router-dom";

import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useDeleteRun, useRun, useRunStatus } from "@/hooks/queries";
import { isTerminalRunStatus } from "@/types";
import type {
  RunDetailResponse,
  RunKnobs,
  RunResult,
  RunStatus,
} from "@/types";

// The run detail (ADR 0011, spec §A.4/§B.4): the launch header, the read-only fixed-knobs
// snapshot, and live progress polled off `GET /api/runs/:id/status` until a terminal state
// (the React-side replacement for HTMX polling, ADR 0006/0010). Once `done`/`failed` the
// poll stops and the per-model Results appear, each linking to its Result drill-down.

export function RunDetail() {
  const { id } = useParams();
  const runId = Number(id);
  const valid = Number.isInteger(runId);

  const detail = useRun(valid ? runId : 0);
  // The live poll: while non-terminal it advances progress; it stops at done/failed.
  const status = useRunStatus(valid ? runId : 0);

  if (!valid) {
    return (
      <Shell>
        <EmptyState title="Run not found" description="That run id is not valid." />
      </Shell>
    );
  }
  if (detail.isLoading) {
    return (
      <Shell>
        <LoadingBlock rows={6} />
      </Shell>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <Shell>
        <ErrorBlock error={detail.error} />
      </Shell>
    );
  }

  // The status poll is the source of truth for live status/progress/results; before its
  // first response, fall back to the detail snapshot so the header renders immediately.
  const live = status.data ?? {
    status: detail.data.run.status,
    progress: detail.data.run.progress,
    total_units: detail.data.run.total_units,
    results: detail.data.results,
  };
  const terminal = isTerminalRunStatus(live.status);

  return (
    <Shell>
      <Header
        detail={detail.data}
        status={live.status}
        progress={live.progress}
        total={live.total_units}
        resultCount={live.results.length}
        terminal={terminal}
      />
      <KnobsSnapshot knobs={detail.data.knobs} />
      <ProgressBlock progress={live.progress} total={live.total_units} />
      <ResultsBlock results={live.results} terminal={terminal} />
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <section className="mx-auto max-w-[1280px]">{children}</section>;
}

function Header({
  detail,
  status,
  progress,
  total,
  resultCount,
  terminal,
}: {
  detail: RunDetailResponse;
  status: RunStatus;
  progress: number;
  total: number;
  resultCount: number;
  terminal: boolean;
}) {
  return (
    <>
      <div className="mb-1.5 text-[12.5px] text-muted-foreground">
        <Link to="/runs" className="hover:text-foreground">
          Runs
        </Link>{" "}
        / Run #{detail.run.id}
      </div>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mb-2 text-xl font-semibold tracking-tight">
            Run #{detail.run.id}{" "}
            <span className="align-middle text-sm font-normal capitalize text-muted-foreground">
              {detail.run.task}
            </span>
          </h1>
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-[12.5px] text-muted-foreground">
              {detail.prompt.family}{" "}
              <span className="font-mono">v{detail.prompt.version}</span>
            </span>
            <Link
              to={`/library/drawings/${detail.drawing.id}`}
              className="text-[12.5px] font-medium text-primary hover:underline"
            >
              Drawing: {detail.drawing.name} ↗
            </Link>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
            <StatusBadge status={status} />
            {progress} / {total}
          </span>
          {/* Only a terminal Run may be deleted: a still-running Run's BackgroundRunner is
              writing Results/overlays, which a mid-flight cascade would race, and its
              result count (the confirm's collateral) isn't final yet. */}
          {terminal && (
            <DeleteRunButton runId={detail.run.id} resultCount={resultCount} />
          )}
        </div>
      </div>
    </>
  );
}

/** Delete this Run and everything under it, after a confirmation that states the collateral
 * (ADR-0016). On success the run history/board/meta are invalidated and we route back to the
 * run list, since this detail page no longer has a Run to show. */
function DeleteRunButton({
  runId,
  resultCount,
}: {
  runId: number;
  resultCount: number;
}) {
  const navigate = useNavigate();
  const deleteRun = useDeleteRun();

  return (
    <ConfirmDeleteDialog
      trigger={
        <Button variant="destructive" size="sm">
          Delete run
        </Button>
      }
      title={`Delete run #${runId}?`}
      description={
        <>
          This permanently deletes run #{runId} and its {resultCount} result
          {resultCount === 1 ? "" : "s"}, removing it from the leaderboard and run
          history.
        </>
      }
      confirmLabel="Delete run"
      pending={deleteRun.isPending}
      error={deleteRun.error}
      onConfirm={() =>
        deleteRun.mutateAsync(runId).then(() => navigate("/runs"))
      }
    />
  );
}

const KNOB_LABELS: { key: keyof RunKnobs; label: string }[] = [
  { key: "dpi", label: "DPI" },
  { key: "downsample_px", label: "Downsample px" },
  { key: "max_tokens", label: "max_tokens" },
  { key: "prefill", label: "prefill" },
  { key: "temperature", label: "temperature" },
];

/** The read-only fixed-knobs snapshot the Run recorded (spec: Runs 18). */
function KnobsSnapshot({ knobs }: { knobs: RunKnobs }) {
  return (
    <div className="mb-6 rounded-lg border bg-card p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Fixed knobs snapshot
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        {KNOB_LABELS.map(({ key, label }) => (
          <div key={key}>
            <div className="mb-0.5 text-[11px] text-muted-foreground">
              {label}
            </div>
            <div className="font-mono text-[13px]">{String(knobs[key])}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ProgressBlock({
  progress,
  total,
}: {
  progress: number;
  total: number;
}) {
  const pct = total > 0 ? Math.round((progress / total) * 100) : 0;
  return <Progress value={pct} aria-label="Run progress" className="mb-6" />;
}

/** The per-model Results. Terminal → each links to its drill-down; otherwise a live note. */
function ResultsBlock({
  results,
  terminal,
}: {
  results: RunResult[];
  terminal: boolean;
}) {
  return (
    <div>
      <h2 className="mb-2.5 text-sm font-semibold">
        Models ({results.length})
      </h2>
      <div className="flex flex-col gap-2">
        {results.map((result) => (
          <div
            key={result.id}
            className="flex items-center justify-between gap-4 rounded-lg border bg-card px-3.5 py-2.5"
          >
            <span className="font-mono text-[12.5px]">{result.model}</span>
            {terminal ? (
              <Link
                to={`/results/${result.id}`}
                className="shrink-0 text-[12.5px] font-medium text-primary hover:underline"
              >
                View result →
              </Link>
            ) : (
              <span className="shrink-0 text-[12px] text-muted-foreground">
                running…
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
