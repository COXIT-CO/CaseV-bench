import { ChevronLeft, ChevronRight, Maximize } from "lucide-react";
import * as React from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

// A reusable in-app image viewer (spec §Image viewer, ticket 08). Built on the vendored
// shadcn Dialog primitive — no new dependency — so Esc/backdrop close and focus trapping
// come for free and it matches the design system (ADR 0012). It takes a list of images and
// a controlled open-index (null = closed) and adds wheel/pinch zoom, drag-to-pan, a
// fit/reset control, on-screen prev/next arrows, and Left/Right keyboard paging with a page
// counter. Read-only: zoom/pan is a CSS transform, there is no annotation or editing.
//
// It replaces the old `<a target="_blank">` open-in-a-new-tab links on the prediction
// overlays (ResultDetail) and the Drawing Page thumbnails (DrawingDetail): clicking a card
// opens the viewer at that image, navigable across the whole set.

export interface LightboxImage {
  /** The image URL (a page or overlay PNG under /api). */
  src: string;
  /** A human label shown in the viewer chrome, e.g. "Page 3". */
  label: string;
}

interface ImageLightboxProps {
  images: LightboxImage[];
  /** The open image's index, or null when the viewer is closed (controlled by the caller). */
  index: number | null;
  /** Called when prev/next moves to a different image in the set. */
  onIndexChange: (index: number) => void;
  /** Called when the viewer is dismissed (Esc, backdrop, or the close button). */
  onClose: () => void;
}

const MIN_SCALE = 1;
const MAX_SCALE = 8;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function ImageLightbox({
  images,
  index,
  onIndexChange,
  onClose,
}: ImageLightboxProps) {
  const open = index !== null;
  const current = open ? images[index] : undefined;

  // Zoom (scale) and pan (offset) live here and reset whenever the shown image changes, so
  // paging always lands back at fit. Transform-only — no pixels are modified. Zoom is
  // anchored at the image centre and the pan offset is not clamped to bounds: this is a
  // read-only viewer, and the Fit control always recovers a lost image, so we keep the
  // gesture math simple rather than tracking a viewport-relative focal point.
  const [scale, setScale] = React.useState(MIN_SCALE);
  const [offset, setOffset] = React.useState({ x: 0, y: 0 });

  const reset = React.useCallback(() => {
    setScale(MIN_SCALE);
    setOffset({ x: 0, y: 0 });
  }, []);

  React.useEffect(() => {
    reset();
  }, [index, reset]);

  const canPrev = index !== null && index > 0;
  const canNext = index !== null && index < images.length - 1;

  const goPrev = React.useCallback(() => {
    if (index !== null && index > 0) onIndexChange(index - 1);
  }, [index, onIndexChange]);

  const goNext = React.useCallback(() => {
    if (index !== null && index < images.length - 1) onIndexChange(index + 1);
  }, [index, images.length, onIndexChange]);

  // Left/Right page across the set; the Dialog already handles Esc to close.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      goPrev();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      goNext();
    }
  };

  // Wheel zoom. The Dialog already locks background scroll, so a plain React handler is
  // enough — no non-passive native listener needed.
  const onWheel = (e: React.WheelEvent) => {
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    setScale((s) => {
      const next = clamp(s * factor, MIN_SCALE, MAX_SCALE);
      if (next === MIN_SCALE) setOffset({ x: 0, y: 0 });
      return next;
    });
  };

  // Drag-to-pan (only meaningful once zoomed in). Pointer capture keeps the drag smooth even
  // if the cursor leaves the image.
  const dragStart = React.useRef<{ x: number; y: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    if (scale <= MIN_SCALE) return;
    dragStart.current = { x: e.clientX - offset.x, y: e.clientY - offset.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragStart.current) return;
    setOffset({
      x: e.clientX - dragStart.current.x,
      y: e.clientY - dragStart.current.y,
    });
  };
  const onPointerUp = (e: React.PointerEvent) => {
    dragStart.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };

  // Pinch zoom: track the distance between two active touches and scale by its change.
  const pinchDist = React.useRef<number | null>(null);
  const touchDistance = (t: React.TouchList) =>
    Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  const onTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 2) pinchDist.current = touchDistance(e.touches);
  };
  const onTouchMove = (e: React.TouchEvent) => {
    if (e.touches.length !== 2 || pinchDist.current === null) return;
    const dist = touchDistance(e.touches);
    const factor = dist / pinchDist.current;
    pinchDist.current = dist;
    setScale((s) => {
      const next = clamp(s * factor, MIN_SCALE, MAX_SCALE);
      if (next === MIN_SCALE) setOffset({ x: 0, y: 0 });
      return next;
    });
  };
  const onTouchEnd = (e: React.TouchEvent) => {
    if (e.touches.length < 2) pinchDist.current = null;
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        onKeyDown={onKeyDown}
        className="h-[92vh] w-[96vw] max-w-[96vw] gap-0 overflow-hidden border-0 bg-black/95 p-0"
      >
        {/* Radix requires a title/description for the dialog to be accessible; the viewer
            is chromeless so both are visually hidden. */}
        <DialogTitle className="sr-only">Image viewer</DialogTitle>
        <DialogDescription className="sr-only">
          {current
            ? `Viewing ${current.label}. Use the arrow keys to move between images.`
            : "Image viewer"}
        </DialogDescription>

        {index !== null && current && (
          <div className="relative flex h-full w-full flex-col">
            {/* Top chrome: the current label, the page counter, and the fit/reset control.
                The Dialog's own close (×) sits at the top-right. */}
            <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-center gap-3 p-3 text-xs text-white/90">
              <span className="pointer-events-auto rounded bg-black/50 px-2 py-1 font-medium">
                {current.label}
              </span>
              <span className="pointer-events-auto rounded bg-black/50 px-2 py-1 font-mono tabular-nums">
                {index + 1} / {images.length}
              </span>
              <button
                type="button"
                onClick={reset}
                aria-label="Reset zoom to fit"
                className="pointer-events-auto flex items-center gap-1.5 rounded bg-black/50 px-2 py-1 font-medium hover:bg-black/70"
              >
                <Maximize className="h-3.5 w-3.5" />
                Fit
              </button>
            </div>

            {/* The zoom/pan stage. Overflow is hidden so a zoomed image is clipped to the
                viewport and panned within it. */}
            <div
              className="flex h-full w-full items-center justify-center overflow-hidden"
              onWheel={onWheel}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerLeave={onPointerUp}
              onTouchStart={onTouchStart}
              onTouchMove={onTouchMove}
              onTouchEnd={onTouchEnd}
            >
              <img
                src={current.src}
                alt={current.label}
                draggable={false}
                style={{
                  transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
                }}
                className={cn(
                  "max-h-full max-w-full select-none object-contain",
                  scale > MIN_SCALE
                    ? "cursor-grab active:cursor-grabbing"
                    : "cursor-default",
                )}
              />
            </div>

            {/* Prev/next arrows — disabled (rather than wrapping) at the ends, matching the
                keyboard behaviour and the page counter. */}
            <button
              type="button"
              onClick={goPrev}
              disabled={!canPrev}
              aria-label="Previous image"
              className="absolute left-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white hover:bg-black/70 disabled:pointer-events-none disabled:opacity-30"
            >
              <ChevronLeft className="h-6 w-6" />
            </button>
            <button
              type="button"
              onClick={goNext}
              disabled={!canNext}
              aria-label="Next image"
              className="absolute right-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white hover:bg-black/70 disabled:pointer-events-none disabled:opacity-30"
            >
              <ChevronRight className="h-6 w-6" />
            </button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
