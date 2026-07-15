import * as React from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";
import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { Button } from "@/components/ui/button";
import { GroundTruthEntry } from "@/routes/library/GroundTruthEntry";
import { useDeleteDrawing, useDrawing } from "@/hooks/queries";
import type { DrawingDetailResponse, DrawingPage } from "@/types";

// The Drawing detail (ADR 0011, spec §A.6/§B.2): the rendered Page thumbnails with their
// full-resolution pixel dims, and the ground-truth entry the scoring payoff hangs off
// (ticket 07). Reached both from the Drawings list and from the contextual "unscored → enter
// ground truth" CTAs on the Leaderboard/Result (ADR 0011), so the ground-truth section is
// the `#ground-truth` anchor those links land on.

export function DrawingDetail() {
  const { id } = useParams();
  const drawingId = Number(id);
  const valid = Number.isInteger(drawingId);

  const { data, isLoading, isError, error } = useDrawing(drawingId, valid);

  // The unscored CTAs on the Leaderboard/Result link here with a `#ground-truth` hash
  // (ADR 0011). React Router v6 doesn't scroll to a hash target on its own, so bring the
  // section into view once the detail has loaded and the anchor exists.
  const { hash } = useLocation();
  React.useEffect(() => {
    if (!data || !hash) return;
    document.getElementById(hash.slice(1))?.scrollIntoView?.();
  }, [data, hash]);

  if (!valid) {
    return (
      <Shell>
        <EmptyState
          title="Drawing not found"
          description="That drawing id is not valid."
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
      <Header detail={data} />
      <GroundTruthEntry drawingId={drawingId} />
      <PageGrid pages={data.pages} />
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <section className="mx-auto max-w-[1280px]">{children}</section>;
}

function Header({ detail }: { detail: DrawingDetailResponse }) {
  return (
    <>
      <div className="mb-1.5 text-[12.5px] text-muted-foreground">
        <Link to="/library/drawings" className="hover:text-foreground">
          Drawings
        </Link>{" "}
        / {detail.drawing.name}
      </div>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {detail.drawing.name}
          </h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {detail.pages.length} page{detail.pages.length === 1 ? "" : "s"}
          </p>
        </div>
        <DeleteDrawingButton
          drawingId={detail.drawing.id}
          name={detail.drawing.name}
          runCount={detail.run_count}
          resultCount={detail.result_count}
        />
      </div>
    </>
  );
}

/** Delete this Drawing and everything derived from it, after a confirmation that states the
 * collateral (ADR-0016): its Pages, ground truth, and every Run/Result that used it. On
 * success the Drawings list/board/runs/meta are invalidated and we route back to the Library,
 * since this detail page no longer has a Drawing to show. */
function DeleteDrawingButton({
  drawingId,
  name,
  runCount,
  resultCount,
}: {
  drawingId: number;
  name: string;
  runCount: number;
  resultCount: number;
}) {
  const navigate = useNavigate();
  const deleteDrawing = useDeleteDrawing();

  return (
    <ConfirmDeleteDialog
      trigger={
        <Button variant="destructive" size="sm">
          Delete drawing
        </Button>
      }
      title={`Delete drawing “${name}”?`}
      description={
        <>
          This permanently deletes drawing “{name}”, its pages and ground truth,
          and the {runCount} run{runCount === 1 ? "" : "s"} / {resultCount} result
          {resultCount === 1 ? "" : "s"} that used it — removing them from the
          leaderboard.
        </>
      }
      confirmLabel="Delete drawing"
      pending={deleteDrawing.isPending}
      error={deleteDrawing.error}
      onConfirm={() =>
        deleteDrawing
          .mutateAsync(drawingId)
          .then(() => navigate("/library/drawings"))
      }
    />
  );
}

/** The rendered Page thumbnails with pixel dimensions; empty → the ingest produced no
 * pages (a degenerate PDF). */
function PageGrid({ pages }: { pages: DrawingPage[] }) {
  if (pages.length === 0) {
    return (
      <EmptyState
        title="No pages"
        description="This drawing has no rendered pages."
      />
    );
  }
  return (
    <div>
      <h2 className="mb-2.5 text-sm font-semibold">Pages</h2>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
        {pages.map((page) => (
          <PageCard key={page.page_number} page={page} />
        ))}
      </div>
    </div>
  );
}

/** One rendered Page: click the image to open it full-size, and — when the Page carries
 * location ground truth — a "View ground truth" link to the GT-only overlay, so ground
 * truth is inspectable from Library independent of any Run (ticket 12). The image is its
 * own anchor and the GT link a sibling so the two never nest. */
function PageCard({ page }: { page: DrawingPage }) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <a
        href={page.image_url}
        target="_blank"
        rel="noreferrer"
        className="block hover:opacity-90"
      >
        <img
          src={page.image_url}
          alt={`Page ${page.page_number}`}
          className="block w-full"
        />
      </a>
      <div className="flex items-center justify-between px-2.5 py-2 text-xs text-muted-foreground">
        <span>Page {page.page_number}</span>
        <span className="font-mono">
          {page.width_px}×{page.height_px} px
        </span>
      </div>
      {page.ground_truth_overlay_url && (
        <a
          href={page.ground_truth_overlay_url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 border-t px-2.5 py-2 text-xs font-medium text-primary hover:underline"
        >
          <span className="h-2.5 w-2.5 rounded-sm bg-success" />
          View ground truth ↗
        </a>
      )}
    </div>
  );
}
