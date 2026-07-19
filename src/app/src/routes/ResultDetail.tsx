import * as React from "react";
import { Link, useParams } from "react-router-dom";

import { ImageLightbox, type LightboxImage } from "@/components/ImageLightbox";
import { KnobsSnapshot } from "@/components/KnobsSnapshot";
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
import {
  useResult,
  useRevertPredictionOverride,
  useSetPredictionOverride,
} from "@/hooks/queries";
import { formatExactMatch, formatRate } from "@/lib/format";
import { LABEL_COLORS } from "@/lib/labelColors";
import { cn } from "@/lib/utils";
import type {
  CountingScore,
  LocationScore,
  ResultDetailResponse,
  ResultPrediction,
  Task,
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
      <KnobsSnapshot knobs={data.knobs} />
      {data.scored ? (
        <ScoreBlock result={data} />
      ) : (
        <UnscoredCta drawingId={data.drawing_id} />
      )}
      {data.task === "location" && <PredictionOverlayGrid result={data} />}
      <PredictionsSection
        resultId={data.result_id}
        task={data.task}
        predictions={data.predictions}
      />
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
 * other page (ok, a salvaged error with boxes, or one whose boxes were edited) has a viewable
 * overlay PNG (ADR 0019/0020). */
function hasOverlay(pred: ResultPrediction): boolean {
  return pred.status === "ok" || pred.box_count > 0 || pred.edited_json !== null;
}

/** A small, stable hash of a string — used only to cache-bust the overlay `<img>` when its
 * `edited_json` changes, so an edit or revert redraws instead of showing the browser's cached
 * copy of the same URL. Not security-sensitive; collisions only cost a missed refresh. */
function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return hash >>> 0;
}

/** The overlay PNG URL for one page. When the boxes were edited (ADR 0020, ticket 07) the
 * server redraws the same URL from `edited_json`, so a content-derived query param busts the
 * browser cache and the corrected boxes actually appear; an unedited page uses the bare URL so
 * its cached run-time overlay is reused. */
function overlaySrc(resultId: number, pred: ResultPrediction): string {
  const base = `/api/results/${resultId}/pages/${pred.page_number}/overlay`;
  return pred.edited_json === null
    ? base
    : `${base}?edited=${hashString(pred.edited_json)}`;
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
    src: overlaySrc(result.result_id, pred),
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
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-y-2">
        <h2 className="text-sm font-semibold">Predicted boxes</h2>
        <OverlayLegend />
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

/** The overlay's per-label colour key (ADR 0021, ticket 06): each ObjectType's fixed colour
 * next to its name, so the class-coloured boxes on the overlay are self-explanatory. Colours
 * mirror the backend renderer via the shared `LABEL_COLORS`. */
function OverlayLegend() {
  return (
    <ul
      aria-label="Overlay colour legend"
      className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-xs text-muted-foreground"
    >
      {LABEL_COLORS.map(({ label, color }) => (
        <li key={label} className="flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: color }}
          />
          {label}
        </li>
      ))}
    </ul>
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
  const src = overlaySrc(resultId, pred);
  const edited = pred.edited_json !== null;
  // A salvaged badge only makes sense for the model's own partial output; an edited page's
  // boxes are the developer's, so the "edited" badge takes over.
  const salvaged = !edited && pred.status === "error" && pred.box_count > 0;
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
          {edited && <EditedBadge />}
        </span>
        <span className="font-mono">{pred.box_count} boxes</span>
      </div>
    </div>
  );
}

/** A small "edited" badge marking a Prediction whose boxes were manually overridden (ADR 0020,
 * ticket 07) — visibly distinct from the model's own output, on both the overlay card and the
 * JSON view. */
function EditedBadge() {
  return (
    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
      edited
    </span>
  );
}

function PredictionsSection({
  resultId,
  task,
  predictions,
}: {
  resultId: number;
  task: Task;
  predictions: ResultPrediction[];
}) {
  return (
    <div>
      <h2 className="mb-2.5 text-sm font-semibold">Per-page predictions</h2>
      <div className="flex flex-col gap-2.5">
        {predictions.map((pred) => (
          <PredictionCard
            key={pred.page_number}
            resultId={resultId}
            task={task}
            pred={pred}
          />
        ))}
      </div>
    </div>
  );
}

function PredictionCard({
  resultId,
  task,
  pred,
}: {
  resultId: number;
  task: Task;
  pred: ResultPrediction;
}) {
  const [editing, setEditing] = React.useState(false);
  const failed = pred.status === "error";
  const edited = pred.edited_json !== null;
  // Edit & redraw is Location-only (a redraw means boxes; counting has no overlay) — ADR 0020.
  const editable = task === "location";
  // A salvaged error still carries best-effort parsed JSON (ADR 0019); once edited, the shown
  // JSON is the developer's override instead — with an "edited", not "salvaged", caption.
  const salvaged = !edited && failed && pred.parsed_json !== null;
  const shownJson = edited ? pred.edited_json : pred.parsed_json;
  const caption = edited ? "Edited output" : salvaged ? "Salvaged output" : null;

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex items-center justify-between gap-2 border-b px-3.5 py-2.5">
        <span className="flex items-center gap-2 text-[12.5px] font-semibold">
          Page {pred.page_number}
          {edited && <EditedBadge />}
        </span>
        <div className="flex items-center gap-2">
          {editable && !editing && (
            <Button
              variant="outline"
              size="sm"
              className="h-6 px-2 text-[11px]"
              onClick={() => setEditing(true)}
            >
              Edit JSON
            </Button>
          )}
          {editable && edited && !editing && (
            <RevertButton resultId={resultId} pageNumber={pred.page_number} />
          )}
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
      </div>

      {failed && !editing && (
        <p className="px-3.5 py-3 text-[12.5px] text-danger">
          {pred.parse_error ?? "Parse error after retry."}
        </p>
      )}

      {editing ? (
        <PredictionJsonEditor
          resultId={resultId}
          pageNumber={pred.page_number}
          initialJson={editorSeed(shownJson)}
          onClose={() => setEditing(false)}
        />
      ) : (
        (!failed || salvaged || edited) &&
        shownJson !== null && (
          <pre
            className={cn(
              "overflow-x-auto whitespace-pre-wrap px-3.5 pb-3 font-mono text-[11.5px] leading-relaxed",
              failed && !edited ? "pt-0" : "pt-3",
            )}
          >
            {caption && (
              <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
                {caption}
              </span>
            )}
            {formatJson(shownJson)}
          </pre>
        )
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

/** Clear the manual override, restoring the model's output and its original overlay (ADR 0020). */
function RevertButton({
  resultId,
  pageNumber,
}: {
  resultId: number;
  pageNumber: number;
}) {
  const revert = useRevertPredictionOverride(resultId);
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-6 px-2 text-[11px] text-muted-foreground"
      disabled={revert.isPending}
      onClick={() => revert.mutate(pageNumber)}
    >
      {revert.isPending ? "Reverting…" : "Revert to model output"}
    </Button>
  );
}

/** The inline box-JSON editor: a developer corrects the `LocationResult` and saves it as a
 * persisted override (ADR 0020, ticket 07). The server validates against the taxonomy and 0–1
 * coords, so a box that won't draw or score is rejected with a precise error and nothing is
 * saved; a clean save redraws the overlay and marks the page "edited". */
function PredictionJsonEditor({
  resultId,
  pageNumber,
  initialJson,
  onClose,
}: {
  resultId: number;
  pageNumber: number;
  initialJson: string;
  onClose: () => void;
}) {
  const [text, setText] = React.useState(initialJson);
  const setOverride = useSetPredictionOverride(resultId);

  function save() {
    setOverride.mutate(
      { pageNumber, editedJson: text },
      { onSuccess: onClose },
    );
  }

  return (
    <div className="flex flex-col gap-2.5 px-3.5 py-3">
      <textarea
        aria-label={`Edit prediction JSON for page ${pageNumber}`}
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={10}
        spellCheck={false}
        className="w-full resize-y rounded-md border bg-background px-2.5 py-2 font-mono text-[11.5px] leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      <p className="text-[11px] text-muted-foreground">
        A <code className="font-mono">LocationResult</code> —{" "}
        <code className="font-mono">{'{"detections": [...]}'}</code> — with taxonomy labels and
        0–1 coordinates. Saving redraws the overlay and never changes the score.
      </p>
      {setOverride.isError && <ErrorBlock error={setOverride.error} />}
      <div className="flex gap-2">
        <Button
          size="sm"
          className="h-7"
          disabled={setOverride.isPending}
          onClick={save}
        >
          {setOverride.isPending ? "Saving…" : "Save & redraw"}
        </Button>
        <Button variant="ghost" size="sm" className="h-7" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** The starting text for the editor: the current boxes pretty-printed, or an empty
 * `LocationResult` skeleton when a page has no boxes yet (e.g. a fully failed prediction) so a
 * developer can add the correct boxes from scratch. */
function editorSeed(json: string | null): string {
  return json ? formatJson(json) : '{\n  "detections": []\n}';
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
