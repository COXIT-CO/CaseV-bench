import * as React from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { ConfirmDeleteDialog } from "@/components/ConfirmDeleteDialog";
import { ImageLightbox, type LightboxImage } from "@/components/ImageLightbox";
import { OverlayLegend } from "@/components/OverlayLegend";
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
      <PageGrid drawingId={drawingId} pages={data.pages} />
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

/** The on-demand ground-truth overlay PNG for one (Drawing, Page) — the imported boxes drawn
 * on the page image, class-coloured (ticket 05's route). A page with no imported ground truth
 * returns the plain page from this same route, so the toggle needs no per-page GT-presence
 * check. */
function gtOverlaySrc(drawingId: number, pageNumber: number): string {
  return `/api/drawings/${drawingId}/pages/${pageNumber}/gt-overlay`;
}

/** The rendered Page thumbnails with pixel dimensions; empty → the ingest produced no
 * pages (a degenerate PDF). A per-drawing toggle overlays the imported ground-truth boxes on
 * every page (ADR 0024, ticket 06) — the benchmark author's check that the un-confirmed expert
 * labeling and the auto-derived reference frame are right — with a per-label colour legend.
 * Clicking a thumbnail opens the in-app lightbox (ticket 08) at the shown variant (plain or
 * overlay), navigable across every Page in the Drawing for zoom/pan. */
function PageGrid({
  drawingId,
  pages,
}: {
  drawingId: number;
  pages: DrawingPage[];
}) {
  // Off by default: the page shows the drawing itself until the author asks to see the boxes.
  const [showOverlay, setShowOverlay] = React.useState(false);
  const [openIndex, setOpenIndex] = React.useState<number | null>(null);

  // Each page's shown image — its ground-truth overlay when the toggle is on, the plain page
  // otherwise — derived once so the cards and the lightbox open the same variant.
  const shown = pages.map((page) => ({
    page,
    src: showOverlay ? gtOverlaySrc(drawingId, page.page_number) : page.image_url,
  }));
  const images: LightboxImage[] = shown.map(({ page, src }) => ({
    src,
    label: `Page ${page.page_number}`,
  }));

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
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-y-2">
        <h2 className="text-sm font-semibold">Pages</h2>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showOverlay}
              onChange={(event) => setShowOverlay(event.target.checked)}
              className="h-3.5 w-3.5 accent-primary"
            />
            Ground-truth overlay
          </label>
          {showOverlay && <OverlayLegend />}
        </div>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3.5">
        {shown.map(({ page, src }, i) => (
          <PageCard
            key={page.page_number}
            page={page}
            src={src}
            onOpen={() => setOpenIndex(i)}
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

/** One rendered Page: click the thumbnail to open it in the in-app lightbox (ticket 08)
 * rather than a new browser tab. `src` is the plain page image or its ground-truth overlay,
 * per the grid's toggle; the alt text stays "Page N" either way. */
function PageCard({
  page,
  src,
  onOpen,
}: {
  page: DrawingPage;
  src: string;
  onOpen: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <button
        type="button"
        onClick={onOpen}
        className="block w-full cursor-zoom-in hover:opacity-90"
      >
        <img src={src} alt={`Page ${page.page_number}`} className="block w-full" />
      </button>
      <div className="flex items-center justify-between px-2.5 py-2 text-xs text-muted-foreground">
        <span>Page {page.page_number}</span>
        <span className="font-mono">
          {page.width_px}×{page.height_px} px
        </span>
      </div>
    </div>
  );
}
