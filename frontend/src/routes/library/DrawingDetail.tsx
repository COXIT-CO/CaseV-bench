import * as React from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { EmptyState, ErrorBlock, LoadingBlock } from "@/components/states";
import { GroundTruthEntry } from "@/routes/library/GroundTruthEntry";
import { useDrawing } from "@/hooks/queries";
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
      <div className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">
          {detail.drawing.name}
        </h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          {detail.pages.length} page{detail.pages.length === 1 ? "" : "s"}
        </p>
      </div>
    </>
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
          <a
            key={page.page_number}
            href={page.image_url}
            target="_blank"
            rel="noreferrer"
            className="block overflow-hidden rounded-lg border bg-card hover:border-primary"
          >
            <img
              src={page.image_url}
              alt={`Page ${page.page_number}`}
              className="block w-full"
            />
            <div className="flex justify-between px-2.5 py-2 text-xs text-muted-foreground">
              <span>Page {page.page_number}</span>
              <span className="font-mono">
                {page.width_px}×{page.height_px} px
              </span>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
