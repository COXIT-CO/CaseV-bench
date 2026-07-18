import * as React from "react";
import { Link, useParams } from "react-router-dom";

import { ImageLightbox, type LightboxImage } from "@/components/ImageLightbox";
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
// Predictions — plus, for location, the prediction overlay grid (the model's boxes on each
// page), shown whether or not the Result is scored (ticket 01). The unscored state (no ground
// truth) additionally shows a prominent CTA toward ground-truth entry.

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
      {data.task === "location" && <PredictionOverlayGrid result={data} />}
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
        <Link to={`/library/drawings/${drawingId}#ground-truth`}>
          Add ground truth
        </Link>
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

/** No overlay was cached only when nothing parsed — an error page with zero boxes. Every
 * other page (ok, or a salvaged error with boxes) has a viewable overlay PNG (ADR 0019). */
function hasOverlay(pred: ResultPrediction): boolean {
  return pred.status === "ok" || pred.box_count > 0;
}

/** The per-page prediction overlays: the model's labeled boxes on each page, served from the
 * cached prediction-overlay route. The only Location overlay now (ticket 01) — shown for
 * every Location Result, scored or not. Clicking a card opens the in-app lightbox (ticket
 * 08) navigable across every viewable overlay in the Result. */
function PredictionOverlayGrid({ result }: { result: ResultDetailResponse }) {
  // The viewable overlays, in page order — the set the lightbox pages through. Failed pages
  // with no cached overlay are excluded (they show a placeholder, not a clickable image).
  const viewable = result.predictions.filter(hasOverlay);
  const images: LightboxImage[] = viewable.map((pred) => ({
    src: `/api/results/${result.result_id}/pages/${pred.page_number}/overlay`,
    label: `Page ${pred.page_number}`,
  }));
  // A page's lightbox start index (its position in `viewable`), so a card can open the
  // viewer at itself even though the grid also renders non-viewable (failed) pages.
  const startIndexByPage = new Map(
    viewable.map((pred, i) => [pred.page_number, i]),
  );
  const [openIndex, setOpenIndex] = React.useState<number | null>(null);

  return (
    <div className="mb-6">
      <div className="mb-2.5 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Predicted boxes</h2>
        <div className="flex items-center gap-3.5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-danger" />
            Prediction
          </span>
        </div>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
        {result.predictions.map((pred) => (
          <PredictionOverlayCard
            key={pred.page_number}
            resultId={result.result_id}
            pred={pred}
            // The card's position within the viewable set (its lightbox start index), or -1
            // when the page has no overlay to open.
            viewIndex={startIndexByPage.get(pred.page_number) ?? -1}
            onOpen={setOpenIndex}
          />
        ))}
      </div>
      <ImageLightbox
        images={images}
        index={openIndex}
        onIndexChange={setOpenIndex}
        onClose={() => setOpenIndex(null)}
      />
    </div>
  );
}

/** One page's prediction-only overlay. A salvaged error page still cached an overlay from
 * the boxes that parsed (ADR 0019), so it shows that overlay with a "salvaged" badge; only a
 * page with no boxes at all falls back to the "prediction failed" placeholder. The overlay
 * opens in the in-app lightbox rather than a new browser tab (ticket 08). */
function PredictionOverlayCard({
  resultId,
  pred,
  viewIndex,
  onOpen,
}: {
  resultId: number;
  pred: ResultPrediction;
  viewIndex: number;
  onOpen: (index: number) => void;
}) {
  const src = `/api/results/${resultId}/pages/${pred.page_number}/overlay`;
  const salvaged = pred.status === "error" && pred.box_count > 0;
  const body = hasOverlay(pred) ? (
    <button
      type="button"
      onClick={() => onOpen(viewIndex)}
      className="block w-full cursor-zoom-in hover:opacity-90"
    >
      <img
        src={src}
        alt={`predicted boxes for page ${pred.page_number}`}
        className="block w-full"
      />
    </button>
  ) : (
    <div className="flex aspect-square items-center justify-center bg-muted">
      <span className="rounded bg-black/40 px-2 py-1 text-[11px] font-semibold text-white">
        prediction failed
      </span>
    </div>
  );
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      {body}
      <div className="flex justify-between px-2.5 py-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          Page {pred.page_number}
          {salvaged && (
            <span className="rounded bg-danger-subtle px-1.5 py-0.5 text-[10px] font-semibold text-danger">
              salvaged
            </span>
          )}
        </span>
        <span className="font-mono">{pred.box_count} boxes</span>
      </div>
    </div>
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
  // A salvaged error still carries best-effort parsed JSON (ADR 0019) — show it alongside
  // the error note so a developer sees what the model attempted, not just a bare failure.
  const salvaged = failed && pred.parsed_json !== null;
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
      {failed && (
        <p className="px-3.5 py-3 text-[12.5px] text-danger">
          {pred.parse_error ?? "Parse error after retry."}
        </p>
      )}
      {(!failed || salvaged) && (
        <pre
          className={cn(
            "overflow-x-auto whitespace-pre-wrap px-3.5 pb-3 font-mono text-[11.5px] leading-relaxed",
            failed ? "pt-0" : "pt-3",
          )}
        >
          {failed && (
            <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
              Salvaged output
            </span>
          )}
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
