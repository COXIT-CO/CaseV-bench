import { Link, useParams } from "react-router-dom";

import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useResult } from "@/hooks/queries";
import { formatExactMatch, formatRate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  CountingScore,
  LocationScore,
  ResultDetailResponse,
  ResultPrediction,
} from "@/types";

// The Result drill-down (ADR 0011, spec §A.3): why a Result scored as it did. A header with
// the Configuration refs, the Score block (counting or location shape), and the per-page
// Predictions — plus, for location, a red-prediction-over-green-GT overlay compare grid.
// The unscored state (no ground truth) shows a prominent CTA toward ground-truth entry.

export function ResultDetail() {
  const { id } = useParams();
  const resultId = Number(id);

  // A non-numeric :id never matched a real Result; treat it as a not-found, not a fetch.
  const valid = Number.isInteger(resultId);
  const { data, isLoading, isError, error } = useResult(valid ? resultId : 0);

  if (!valid) {
    return (
      <Shell>
        <EmptyState
          title="Result not found"
          description="That result id is not valid."
        />
      </Shell>
    );
  }
  if (isLoading) {
    return (
      <Shell>
        <LoadingBlock rows={6} />
      </Shell>
    );
  }
  if (isError || !data) {
    return (
      <Shell>
        <ErrorBlock error={error} />
      </Shell>
    );
  }

  return (
    <Shell>
      <Header result={data} />
      {data.scored ? (
        <ScoreBlock result={data} />
      ) : (
        <UnscoredCta drawingId={data.drawing_id} />
      )}
      {data.scored && data.task === "location" && (
        <OverlayGrid result={data} />
      )}
      <PredictionsSection predictions={data.predictions} />
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <section className="mx-auto max-w-[1280px]">{children}</section>;
}

function Header({ result }: { result: ResultDetailResponse }) {
  return (
    <>
      <div className="mb-1.5 text-[12.5px] text-muted-foreground">
        <Link to="/" className="hover:text-foreground">
          Leaderboard
        </Link>{" "}
        / Result #{result.result_id}
      </div>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mb-2 text-xl font-semibold tracking-tight">
            Result #{result.result_id}
          </h1>
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="rounded-md border bg-card px-2 py-0.5 font-mono text-[12.5px]">
              {result.model}
            </span>
            <span className="text-[12.5px] text-muted-foreground">
              {result.prompt_family}{" "}
              <span className="font-mono">v{result.prompt_version}</span>
            </span>
            <Link
              to={`/library/drawings/${result.drawing_id}`}
              className="text-[12.5px] font-medium text-primary hover:underline"
            >
              Drawing: {result.drawing_name} ↗
            </Link>
            <Link
              to={`/runs/${result.run_id}`}
              className="text-[12.5px] font-medium text-primary hover:underline"
            >
              Run #{result.run_id} ↗
            </Link>
          </div>
        </div>
      </div>
    </>
  );
}

/** The unscored state: distinct from a zero score, with a CTA toward ground-truth entry. */
function UnscoredCta({ drawingId }: { drawingId: number }) {
  return (
    <div className="mb-6 rounded-lg border border-dashed p-10 text-center">
      <p className="text-sm font-semibold">
        No ground truth for this drawing yet
      </p>
      <p className="mx-auto mt-1.5 max-w-md text-[13px] text-muted-foreground">
        This prediction can’t be scored until ground truth is added.
      </p>
      <Button asChild size="sm" className="mt-4">
        <Link to={`/library/drawings/${drawingId}`}>Add ground truth</Link>
      </Button>
    </div>
  );
}

function ScoreBlock({ result }: { result: ResultDetailResponse }) {
  return (
    <div className="mb-6 rounded-lg border bg-card p-5">
      {result.location_score ? (
        <LocationScoreBlock score={result.location_score} />
      ) : result.counting_score ? (
        <CountingScoreBlock
          score={result.counting_score}
          labelCount={result.label_count}
        />
      ) : null}
    </div>
  );
}

/** One big headline metric in a Score block. */
function Stat({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <div>
      <div className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "font-mono text-[26px] font-bold",
          emphasis && "text-success",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function CountingScoreBlock({
  score,
  labelCount,
}: {
  score: CountingScore;
  labelCount: number;
}) {
  return (
    <>
      <div className="mb-5 flex gap-9">
        <Stat
          label="Total abs. error"
          value={String(score.total_absolute_error)}
          emphasis={score.total_absolute_error === 0}
        />
        <Stat
          label="Exact matches"
          value={formatExactMatch(score.exact_match_count, labelCount)}
        />
      </div>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Label</TableHead>
              <TableHead className="text-right">Predicted (summed)</TableHead>
              <TableHead className="text-right">Ground truth</TableHead>
              <TableHead className="text-right">Abs. error</TableHead>
              <TableHead className="text-right">Exact?</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {score.per_label.map((ls) => (
              <TableRow key={ls.label} className="hover:bg-transparent">
                <TableCell className="font-medium">{ls.label}</TableCell>
                <TableCell className="text-right font-mono">
                  {ls.predicted}
                </TableCell>
                <TableCell className="text-right font-mono">{ls.gt}</TableCell>
                <TableCell
                  className={cn(
                    "text-right font-mono",
                    ls.absolute_error === 0 ? "text-success" : "text-danger",
                  )}
                >
                  {ls.absolute_error}
                </TableCell>
                <TableCell className="text-right">
                  <ExactBadge exact={ls.exact_match} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  );
}

function ExactBadge({ exact }: { exact: boolean }) {
  return (
    <span
      className={cn(
        "rounded px-2 py-0.5 text-[11px] font-semibold",
        exact
          ? "bg-success-subtle text-success"
          : "bg-danger-subtle text-danger",
      )}
    >
      {exact ? "yes" : "no"}
    </span>
  );
}

function LocationScoreBlock({ score }: { score: LocationScore }) {
  return (
    <>
      <div className="mb-4 flex gap-9">
        <Stat label="Precision" value={formatRate(score.precision)} />
        <Stat label="Recall" value={formatRate(score.recall)} />
        <Stat label="F1" value={formatRate(score.f1)} emphasis />
      </div>
      <p className="mb-4 text-xs text-muted-foreground">
        IoU@0.5, matched per page then micro-averaged.
      </p>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Label</TableHead>
              <TableHead className="text-right">TP</TableHead>
              <TableHead className="text-right">FP</TableHead>
              <TableHead className="text-right">FN</TableHead>
              <TableHead className="text-right">P</TableHead>
              <TableHead className="text-right">R</TableHead>
              <TableHead className="text-right">F1</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {score.per_label.map((ls) => (
              <TableRow key={ls.label} className="hover:bg-transparent">
                <TableCell className="font-medium">{ls.label}</TableCell>
                <TableCell className="text-right font-mono">{ls.tp}</TableCell>
                <TableCell className="text-right font-mono text-danger">
                  {ls.fp}
                </TableCell>
                <TableCell className="text-right font-mono text-danger">
                  {ls.fn}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {formatRate(ls.precision)}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {formatRate(ls.recall)}
                </TableCell>
                <TableCell className="text-right font-mono font-semibold">
                  {formatRate(ls.f1)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  );
}

/** The per-page GT-vs-prediction compare overlays: predicted boxes (red) over ground-truth
 * boxes (green), click-to-enlarge, flagging any page with no ground truth. */
function OverlayGrid({ result }: { result: ResultDetailResponse }) {
  return (
    <div className="mb-6">
      <div className="mb-2.5 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Ground truth vs. prediction</h2>
        <div className="flex items-center gap-3.5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-danger" />
            Prediction
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-success" />
            Ground truth
          </span>
        </div>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
        {result.predictions.map((pred) => (
          <OverlayCard
            key={pred.page_number}
            resultId={result.result_id}
            pred={pred}
          />
        ))}
      </div>
    </div>
  );
}

function OverlayCard({
  resultId,
  pred,
}: {
  resultId: number;
  pred: ResultPrediction;
}) {
  const src = `/api/results/${resultId}/pages/${pred.page_number}/compare-overlay`;
  return (
    <a
      href={src}
      target="_blank"
      rel="noreferrer"
      className="block overflow-hidden rounded-lg border bg-card hover:border-primary"
    >
      <div className="relative">
        <img
          src={src}
          alt={`prediction vs ground truth for page ${pred.page_number}`}
          className="block w-full"
        />
        {!pred.has_gt && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/45">
            <span className="rounded bg-black/40 px-2 py-1 text-[11px] font-semibold text-white">
              no ground truth
            </span>
          </div>
        )}
      </div>
      <div className="flex justify-between px-2.5 py-2 text-xs text-muted-foreground">
        <span>Page {pred.page_number}</span>
        <span className="font-mono">{pred.box_count} boxes</span>
      </div>
    </a>
  );
}

function PredictionsSection({
  predictions,
}: {
  predictions: ResultPrediction[];
}) {
  return (
    <div>
      <h2 className="mb-2.5 text-sm font-semibold">Per-page predictions</h2>
      <div className="flex flex-col gap-2.5">
        {predictions.map((pred) => (
          <PredictionCard key={pred.page_number} pred={pred} />
        ))}
      </div>
    </div>
  );
}

function PredictionCard({ pred }: { pred: ResultPrediction }) {
  const failed = pred.status === "error";
  return (
    <div className="rounded-lg border bg-card">
      <div className="flex items-center justify-between border-b px-3.5 py-2.5">
        <span className="text-[12.5px] font-semibold">
          Page {pred.page_number}
        </span>
        <span
          className={cn(
            "rounded px-2 py-0.5 text-[11px] font-semibold",
            failed
              ? "bg-danger-subtle text-danger"
              : "bg-success-subtle text-success",
          )}
        >
          {pred.status}
        </span>
      </div>
      {failed ? (
        <p className="px-3.5 py-3 text-[12.5px] text-danger">
          {pred.parse_error ?? "Parse error after retry."}
        </p>
      ) : (
        <pre className="overflow-x-auto whitespace-pre-wrap px-3.5 py-3 font-mono text-[11.5px] leading-relaxed">
          {formatJson(pred.parsed_json)}
        </pre>
      )}
      {pred.raw_content !== null && (
        <details className="border-t px-3.5 py-2">
          <summary className="cursor-pointer text-[11.5px] text-muted-foreground">
            Raw model output
          </summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed text-muted-foreground">
            {pred.raw_content}
          </pre>
        </details>
      )}
    </div>
  );
}

/** Pretty-print the stored parsed JSON; fall back to the raw string if it won't parse. */
function formatJson(raw: string | null): string {
  if (!raw) return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
