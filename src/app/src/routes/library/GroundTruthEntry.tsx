import * as React from "react";

import { ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  useCountingGroundTruth,
  useImportLocationGroundTruth,
  useSaveCountingGroundTruth,
} from "@/hooks/queries";
import type { CountingGtLabel, LocationImportResponse } from "@/types";

// The scoring payoff (ticket 07, spec §A.6): recording ground truth for a Drawing turns its
// previously-unscored Leaderboard/Result rows into scored ones with **no re-run** (scores
// recompute on read). Two entry points hang off the Drawing detail — a counting number-per-
// object-type form pre-filled with any existing totals, and a native `objects` location import
// (an upload affordance; there is no in-app box editor, ADR 0003/0022) that reports problems
// instead of silently dropping them. The contextual "unscored → enter ground truth" CTAs on
// the Leaderboard/Result land on this section (ADR 0011).

const NUMBER_FIELD_CLASS =
  "w-24 rounded-md border bg-background px-2.5 py-1.5 text-right font-mono text-[12.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const FILE_FIELD_CLASS =
  "w-full rounded-md border bg-card px-2.5 py-2 text-[13px] file:mr-3 file:rounded file:border-0 file:bg-muted file:px-2.5 file:py-1 file:text-[12px] file:font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function GroundTruthEntry({ drawingId }: { drawingId: number }) {
  return (
    <section id="ground-truth" className="mb-6 scroll-mt-6">
      <div className="mb-2.5">
        <h2 className="text-sm font-semibold">Ground truth</h2>
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          Record ground truth so this drawing's results become scored — with no re-run. Enter
          counting totals per object type, or import location boxes from an expert's JSON file.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <CountingCard drawingId={drawingId} />
        <LocationImportCard drawingId={drawingId} />
      </div>
    </section>
  );
}

/** The counting-GT entry point: pre-fill the stored totals, then upsert (spec §A.6). Loading
 * is held until the pre-fill arrives so the form seeds its inputs with the existing totals. */
function CountingCard({ drawingId }: { drawingId: number }) {
  const { data, isLoading, isError, error } = useCountingGroundTruth(drawingId);
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-sm font-semibold">Counting totals</div>
      <p className="mb-3.5 mt-1 text-[12px] text-muted-foreground">
        One total per object type across the whole drawing.
      </p>
      {isLoading ? (
        <LoadingBlock rows={4} />
      ) : isError || !data ? (
        <ErrorBlock error={error} />
      ) : (
        <CountingForm drawingId={drawingId} labels={data.labels} />
      )}
    </div>
  );
}

/** One integer input per taxonomy label, seeded from the pre-fill; every label is required
 * before the upsert is allowed (the server enforces the same rule with a 400). */
function CountingForm({
  drawingId,
  labels,
}: {
  drawingId: number;
  labels: CountingGtLabel[];
}) {
  const [values, setValues] = React.useState<Record<string, string>>(() =>
    Object.fromEntries(
      labels.map((label) => [
        label.name,
        label.value === null ? "" : String(label.value),
      ]),
    ),
  );
  const save = useSaveCountingGroundTruth(drawingId);

  const allFilled = labels.every((label) =>
    /^\d+$/.test((values[label.name] ?? "").trim()),
  );
  const canSubmit = allFilled && !save.isPending;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    const totals = Object.fromEntries(
      labels.map((label) => [label.name, Number(values[label.name])]),
    );
    save.mutate(totals);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <div className="flex flex-col gap-2.5">
        {labels.map((label) => (
          <div
            key={label.name}
            className="flex items-center justify-between gap-3"
          >
            <label htmlFor={`gt-${label.name}`} className="font-mono text-[12.5px]">
              {label.name}
            </label>
            <input
              id={`gt-${label.name}`}
              type="number"
              min={0}
              step={1}
              inputMode="numeric"
              value={values[label.name]}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, [label.name]: event.target.value }))
              }
              className={NUMBER_FIELD_CLASS}
            />
          </div>
        ))}
      </div>

      {save.isError && <ErrorBlock error={save.error} />}
      {save.isSuccess && (
        <p role="status" className="text-[12px] font-medium text-success">
          Totals saved — results for this drawing are now scored.
        </p>
      )}

      <Button
        type="submit"
        size="sm"
        disabled={!canSubmit}
        className="self-start"
      >
        {save.isPending ? "Saving…" : "Save totals"}
      </Button>
    </form>
  );
}

/** The location-GT entry point: upload a native `objects` JSON and surface the importer's
 * problem report (spec §A.6). No in-app box editor — annotation happens in an external tool
 * (ADR 0003); this only imports its export, needing no configuration (ADR 0022). */
function LocationImportCard({ drawingId }: { drawingId: number }) {
  const [file, setFile] = React.useState<File | null>(null);
  const importGt = useImportLocationGroundTruth(drawingId);

  const canSubmit = file !== null && !importGt.isPending;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (file === null || importGt.isPending) return;
    importGt.mutate({ file });
  }

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-sm font-semibold">Location boxes (JSON import)</div>
      <p className="mb-3.5 mt-1 text-[12px] text-muted-foreground">
        Import bounding boxes from your expert's labeling JSON. Re-importing replaces this
        drawing's existing boxes.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div>
          <label
            htmlFor="location-gt-file"
            className="mb-1.5 block text-xs font-semibold text-muted-foreground"
          >
            Location JSON file
          </label>
          <input
            id="location-gt-file"
            type="file"
            accept="application/json,.json"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className={FILE_FIELD_CLASS}
          />
        </div>

        {importGt.isError && <ErrorBlock error={importGt.error} />}

        <Button
          type="submit"
          size="sm"
          disabled={!canSubmit}
          className="self-start"
        >
          {importGt.isPending ? "Importing…" : "Import JSON"}
        </Button>
      </form>

      {importGt.isSuccess && importGt.data && (
        <ImportReport result={importGt.data} />
      )}
    </div>
  );
}

/** The importer's report: how many boxes were created, and every problem (off-taxonomy
 * category / unknown page / out-of-frame box) it surfaced rather than silently dropping
 * (spec §A.6). */
function ImportReport({ result }: { result: LocationImportResponse }) {
  return (
    <div
      role="status"
      className="mt-3.5 rounded-md border bg-muted/40 p-3 text-[12px]"
    >
      <p className="font-medium">
        Imported {result.created} box{result.created === 1 ? "" : "es"}.
      </p>
      {result.problems.length === 0 ? (
        <p className="mt-1 text-muted-foreground">No problems reported.</p>
      ) : (
        <>
          <p className="mt-1.5 font-medium text-destructive">
            {result.problems.length} not imported:
          </p>
          <ul className="mt-1 flex flex-col gap-1">
            {result.problems.map((problem, index) => (
              <li key={index} className="flex gap-2">
                <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                  {problem.kind}
                </span>
                <span className="text-muted-foreground">{problem.detail}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
