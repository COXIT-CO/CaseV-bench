import * as React from "react";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import { useDrawings, useUploadDrawing } from "@/hooks/queries";
import type { DrawingSummary } from "@/types";

// The Library Drawings list + upload (ADR 0011, spec §A.6/§B.2): the lower-frequency
// management area under the secondary Library menu. Each Drawing links to its detail
// (page thumbnails + the ground-truth entry point, ticket 07); uploading a PDF ingests it
// through `POST /api/drawings` and routes to the new Drawing so the developer can go
// straight to entering ground truth.

export function Drawings() {
  const { data, isLoading, isError, error } = useDrawings();

  return (
    <section className="mx-auto max-w-[1280px]">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">Drawings</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Upload a PDF, then open it to review its rendered pages and enter ground truth so
          runs against it can be scored.
        </p>
      </header>

      {isLoading ? (
        <LoadingBlock rows={5} />
      ) : isError || !data ? (
        <ErrorBlock error={error} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
          <DrawingList drawings={data.drawings} />
          <UploadCard />
        </div>
      )}
    </section>
  );
}

/** The Drawings, newest-first; empty → guide the developer to upload their first PDF. */
function DrawingList({ drawings }: { drawings: DrawingSummary[] }) {
  if (drawings.length === 0) {
    return (
      <EmptyState
        title="No drawings yet"
        description="Upload a PDF with the form to add your first drawing."
      />
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {drawings.map((drawing) => (
        <Link
          key={drawing.id}
          to={`/library/drawings/${drawing.id}`}
          className="flex items-center justify-between gap-4 rounded-lg border bg-card px-3.5 py-3 transition-colors hover:border-primary/50"
        >
          <span className="font-medium">{drawing.name}</span>
          <span className="text-[12px] text-muted-foreground">
            {drawing.page_count} page{drawing.page_count === 1 ? "" : "s"}
          </span>
        </Link>
      ))}
    </div>
  );
}

/** The upload form: pick a PDF, ingest it, and route to the created Drawing's detail. The
 * ingest renders every page, so the pending state is held until the server responds. */
function UploadCard() {
  const navigate = useNavigate();
  const [file, setFile] = React.useState<File | null>(null);
  const uploadDrawing = useUploadDrawing();

  const canSubmit = file !== null && !uploadDrawing.isPending;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (file === null || uploadDrawing.isPending) return;
    uploadDrawing.mutate(file, {
      onSuccess: (created) => navigate(`/library/drawings/${created.id}`),
    });
  }

  return (
    <form
      onSubmit={submit}
      className="flex h-fit flex-col gap-4 rounded-lg border bg-card p-4"
    >
      <div className="text-sm font-semibold">Upload a drawing</div>

      <div>
        <label
          htmlFor="drawing-file"
          className="mb-1.5 block text-xs font-semibold text-muted-foreground"
        >
          PDF file
        </label>
        <input
          id="drawing-file"
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="w-full rounded-md border bg-card px-2.5 py-2 text-[13px] file:mr-3 file:rounded file:border-0 file:bg-muted file:px-2.5 file:py-1 file:text-[12px] file:font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {uploadDrawing.isError && <ErrorBlock error={uploadDrawing.error} />}

      <p className="text-[11.5px] text-muted-foreground">
        Each page is rendered and downsampled once on upload, so this can take a moment.
      </p>
      <Button type="submit" disabled={!canSubmit}>
        {uploadDrawing.isPending ? "Uploading…" : "Upload"}
      </Button>
    </form>
  );
}
