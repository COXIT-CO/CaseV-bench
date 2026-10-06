"""Renders every page of an uploaded PDF, sends each to a model, and draws the
predicted boxes onto the page. No dataset, no ground truth, no scoring, nothing
written to disk beyond what pymupdf/Pillow need in memory."""

from __future__ import annotations

import functools
import io
import logging
import os
import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pymupdf
from PIL import Image

from core.client import GenerationParams, ModelClient, ModelClientError, ModelResponse
from core.config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_TEMPERATURE, PROMPT_PATH
from core.parse import ResponseParser
from core.render import PageRenderer
from core.scoring import Box
from location_overlay import assign, render as render_overlay

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 20.0

MAX_PAGES = int(os.environ.get("DEMO_APP_MAX_PAGES", 25))
# How many pages' render+model-call+parse run at once — see detect()'s `threads` param.
DEFAULT_THREADS = int(os.environ.get("DEMO_APP_THREADS", 4))

PROMPT_TEXT = PROMPT_PATH.read_text()

ProgressCallback = Callable[[str], None]
PageCallback = Callable[["PageResult"], None]

_NOOP_PROGRESS: ProgressCallback = lambda _message: None  # noqa: E731
_NOOP_PAGE: PageCallback = lambda _result: None  # noqa: E731

# A dedicated, non-propagating logger: printing timing breakdowns should work the same whether
# this runs under `uvicorn --reload` or plain `uvicorn`, regardless of how either configures the
# root logger, and never double-print if the module gets re-imported (e.g. on --reload).
logger = logging.getLogger("casev.demo.detect")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [timing] %(message)s", "%H:%M:%S"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@dataclass(frozen=True, slots=True)
class PageResult:
    page: int
    width: int
    height: int
    original_png: bytes
    # One transparent-background PNG per distinct object type in `boxes` — just that
    # type's boxes, nothing else — so the frontend can show/hide a type by toggling
    # which layer is visible instead of asking for a fresh render. See _label_layers().
    label_layers: dict[str, bytes]
    colors: dict[str, str]  # object_type -> "#rrggbb", the colour each layer was drawn in
    boxes: list[Box]
    dropped: int
    complete: bool
    status: str  # "ok" | "failed"
    elapsed_seconds: float
    error: str | None = None


def _backoff_delay(retry_after: float | None, attempt: int) -> float:
    if retry_after is not None:
        return max(retry_after, 0.0)
    cap = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    return random.uniform(0.0, cap)


def _generate_with_retries(
    client: ModelClient,
    *,
    model: str,
    prompt: str,
    image_bytes: bytes,
    params: GenerationParams,
    max_attempts: int,
    page_number: int,
    total_pages: int,
    on_progress: ProgressCallback,
) -> ModelResponse:
    attempt = 0
    while True:
        attempt += 1
        attempt_started = time.monotonic()
        try:
            response = client.generate(
                model=model,
                prompt=prompt,
                image_bytes=image_bytes,
                image_mime_type="image/png",
                params=params,
            )
            logger.info(
                "page %d/%d: model call attempt %d/%d succeeded in %.2fs "
                "(usage: %d prompt + %d completion tokens)",
                page_number,
                total_pages,
                attempt,
                max_attempts,
                time.monotonic() - attempt_started,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
            return response
        except ModelClientError as exc:
            call_elapsed = time.monotonic() - attempt_started
            if attempt >= max_attempts:
                logger.info(
                    "page %d/%d: model call attempt %d/%d failed in %.2fs, giving up — %s",
                    page_number,
                    total_pages,
                    attempt,
                    max_attempts,
                    call_elapsed,
                    exc,
                )
                raise
            delay = _backoff_delay(exc.retry_after, attempt)
            logger.info(
                "page %d/%d: model call attempt %d/%d failed in %.2fs — %s; backing off %.2fs",
                page_number,
                total_pages,
                attempt,
                max_attempts,
                call_elapsed,
                exc,
                delay,
            )
            on_progress(
                f"Page {page_number} of {total_pages}: {model} error, "
                f"retrying (attempt {attempt + 1} of {max_attempts})…"
            )
            time.sleep(delay)


def _hex(color: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % color


def _label_layers(original_png: bytes, boxes: list[Box]) -> tuple[dict[str, bytes], dict[str, str]]:
    """One transparent-background PNG per distinct object type in `boxes`, plus the colour
    each type was drawn in (as hex, for the frontend's legend). Every box is still drawn by
    location_overlay — the browser only ever decides which pre-rendered layer to show, so
    the interactive legend's toggle is instant and never needs a fresh render.

    Colours are resolved once across every type on the page (`assign`) and the same mapping
    is passed to every render() call below — a type can be displaced from its preferred
    colour by a collision with another type on the same page (see location_overlay's
    README), and that only comes out consistent across these separate per-type calls if
    they all see the full picture up front rather than each re-deriving it from its own
    one-type subset.
    """
    if not boxes:
        return {}, {}

    page_colors = assign(box["object_type"] for box in boxes)
    by_type: dict[str, list[Box]] = {}
    for box in boxes:
        by_type.setdefault(box["object_type"], []).append(box)

    with Image.open(io.BytesIO(original_png)) as page_image:
        layers: dict[str, bytes] = {}
        for object_type, type_boxes in by_type.items():
            layer = render_overlay(page_image, type_boxes, background=False, colors=page_colors)
            buffer = io.BytesIO()
            layer.save(buffer, format="PNG")
            layers[object_type] = buffer.getvalue()

    return layers, {label: _hex(color) for label, color in page_colors.items()}


def _page_meta(result: PageResult) -> dict[str, object]:
    """The small, storable summary of a page — status and boxes, deliberately excluding
    the (large) rendered images. What history.save_run persists, and what detect()
    returns instead of the full PageResults themselves (see detect()'s docstring)."""
    return {
        "page": result.page,
        "status": result.status,
        "error": result.error,
        "dropped": result.dropped,
        "complete": result.complete,
        "boxes": list(result.boxes),
        "elapsed_seconds": result.elapsed_seconds,
    }


def _process_page(
    page_number: int,
    *,
    pdf_bytes: bytes,
    max_px: int,
    client: ModelClient,
    model: str,
    prompt: str,
    params: GenerationParams,
    max_attempts: int,
    total_pages: int,
    on_progress: ProgressCallback,
    on_page: PageCallback,
) -> dict[str, object]:
    page_started = time.monotonic()
    on_progress(f"Page {page_number} of {total_pages}: rendering…")
    render_started = time.monotonic()
    # A fresh Document per task, not one shared across pages: PyMuPDF/MuPDF documents
    # aren't safe to render from multiple threads at once. Reopening from the
    # already-in-memory bytes just reparses the xref table — cheap next to the
    # rendering and model call each task then does.
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        rendered = PageRenderer.render_page(document, page_number, max_px)
    logger.info(
        "page %d/%d: rendered in %.3fs (%dx%d, %.0f dpi)",
        page_number,
        total_pages,
        time.monotonic() - render_started,
        rendered.width,
        rendered.height,
        rendered.effective_dpi,
    )

    on_progress(f"Page {page_number} of {total_pages}: waiting for {model}…")
    try:
        response = _generate_with_retries(
            client,
            model=model,
            prompt=prompt,
            image_bytes=rendered.png_bytes,
            params=params,
            max_attempts=max_attempts,
            page_number=page_number,
            total_pages=total_pages,
            on_progress=on_progress,
        )
    except ModelClientError as exc:
        page_elapsed = time.monotonic() - page_started
        on_progress(
            f"Page {page_number} of {total_pages}: failed — {exc} ({page_elapsed:.1f}s)"
        )
        result = PageResult(
            page=page_number,
            width=rendered.width,
            height=rendered.height,
            original_png=rendered.png_bytes,
            label_layers={},
            colors={},
            boxes=[],
            dropped=0,
            complete=False,
            status="failed",
            elapsed_seconds=page_elapsed,
            error=str(exc),
        )
        on_page(result)
        return _page_meta(result)

    on_progress(f"Page {page_number} of {total_pages}: response received, parsing…")
    parse_started = time.monotonic()
    parsed = ResponseParser().parse_response(response.text, page_number)
    parse_elapsed = time.monotonic() - parse_started

    layers_started = time.monotonic()
    label_layers, colors = _label_layers(rendered.png_bytes, parsed.boxes)
    layers_elapsed = time.monotonic() - layers_started

    page_elapsed = time.monotonic() - page_started
    logger.info(
        "page %d/%d: done in %.2fs total (parse=%.3fs layers=%.3fs, %d box(es), %d dropped)",
        page_number,
        total_pages,
        page_elapsed,
        parse_elapsed,
        layers_elapsed,
        len(parsed.boxes),
        parsed.dropped,
    )
    on_progress(
        f"Page {page_number} of {total_pages}: done — {len(parsed.boxes)} object(s) found "
        f"({page_elapsed:.1f}s)"
    )
    result = PageResult(
        page=page_number,
        width=rendered.width,
        height=rendered.height,
        original_png=rendered.png_bytes,
        label_layers=label_layers,
        colors=colors,
        boxes=parsed.boxes,
        dropped=parsed.dropped,
        complete=parsed.complete,
        status="ok",
        elapsed_seconds=page_elapsed,
        error=None,
    )
    on_page(result)
    return _page_meta(result)


def detect(
    pdf_bytes: bytes,
    *,
    client: ModelClient,
    model: str,
    max_px: int,
    prompt: str = PROMPT_TEXT,
    threads: int = DEFAULT_THREADS,
    on_progress: ProgressCallback | None = None,
    on_page: PageCallback | None = None,
) -> list[dict[str, object]]:
    """Renders and detects every page. Calls `on_page` with each page's full
    PageResult — the original image and its per-label overlay layers included — as
    soon as that page is ready, from whichever worker thread finished it; the caller
    is expected to consume or stream it immediately rather than hold onto it.

    That's deliberate: a page's images are tens of MB at inference resolution, and
    the old version of this function rendered every page up front and accumulated
    every PageResult into one list before returning, so peak memory scaled with page
    count — a 23-page run held >20 pages' worth of images in memory simultaneously,
    which is what was OOM-killing the process on a small Railway instance. Now the
    only thing detect() itself accumulates is this function's *return value* — the
    small `_page_meta` summary (status/boxes, no images) per page, the same shape
    history.save_run persists — so peak memory scales with `threads`, not page count.

    `threads` is how many pages run through render → model call → parse → layers at
    once, capped at `page_count` regardless of what's passed — all pages are submitted
    to the pool up front, not released one at a time, so this is the only thing
    limiting how many are in flight simultaneously. Defaults to `DEFAULT_THREADS`
    (env `DEMO_APP_THREADS`, default 4).
    """
    progress = on_progress or _NOOP_PROGRESS
    page_cb = on_page or _NOOP_PAGE
    run_started = time.monotonic()

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as probe:
        page_count = probe.page_count
    if page_count == 0:
        raise ValueError("the PDF has no pages")
    if page_count > MAX_PAGES:
        raise ValueError(f"the PDF has {page_count} pages; this demo processes at most {MAX_PAGES}")

    progress(f"Sending {page_count} page(s) to {model}…")
    call = functools.partial(
        _process_page,
        pdf_bytes=pdf_bytes,
        max_px=max_px,
        client=client,
        model=model,
        prompt=prompt,
        params=GenerationParams(DEFAULT_TEMPERATURE, DEFAULT_MAX_OUTPUT_TOKENS),
        max_attempts=MAX_ATTEMPTS,
        total_pages=page_count,
        on_progress=progress,
        on_page=page_cb,
    )
    worker_count = min(threads, page_count)
    dispatch_started = time.monotonic()
    pages_meta: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(call, page_number) for page_number in range(1, page_count + 1)]
        for future in as_completed(futures):
            pages_meta.append(future.result())
    logger.info(
        "all %d page(s) processed in %.3fs wall clock (%d threads)",
        page_count,
        time.monotonic() - dispatch_started,
        worker_count,
    )

    total_elapsed = time.monotonic() - run_started
    logger.info("detect() total: %.3fs for %d page(s)", total_elapsed, page_count)
    progress(f"Finishing up… (total {total_elapsed:.1f}s)")
    pages_meta.sort(key=lambda meta: meta["page"])  # type: ignore[arg-type,return-value]
    return pages_meta


def _rebuild_one_page(pdf_bytes: bytes, render_px: int, meta: dict[str, object]) -> PageResult:
    page_number = int(meta["page"])  # type: ignore[arg-type]
    boxes = meta.get("boxes") or []
    # A fresh Document per task, not a shared one: PyMuPDF/MuPDF documents aren't safe to
    # render from multiple threads at once. Reopening from the already-in-memory bytes is
    # cheap (parses the xref table, doesn't re-decode any page) compared to the rendering
    # and overlay work each task then does, so this doesn't cost the concurrency it buys.
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        rendered = PageRenderer.render_page(document, page_number, render_px)
    label_layers, colors = _label_layers(rendered.png_bytes, boxes)  # type: ignore[arg-type]
    return PageResult(
        page=page_number,
        width=rendered.width,
        height=rendered.height,
        original_png=rendered.png_bytes,
        label_layers=label_layers,
        colors=colors,
        boxes=boxes,  # type: ignore[arg-type]
        dropped=int(meta.get("dropped") or 0),
        complete=bool(meta.get("complete", True)),
        status=str(meta["status"]),
        elapsed_seconds=float(meta.get("elapsed_seconds") or 0.0),
        error=meta.get("error"),  # type: ignore[arg-type]
    )


def rebuild_pages(
    pdf_bytes: bytes, render_px: int, pages_meta: list[dict[str, object]], threads: int = 8
) -> list[PageResult]:
    """Replays a history entry without calling the model: re-renders each stored page
    from the original PDF and redraws the overlay from its stored boxes. `pages_meta`
    is one dict per page — `{page, status, error, dropped, complete, boxes,
    elapsed_seconds}` — exactly what history.save_run persisted. `elapsed_seconds` is
    carried through as-is rather than recomputed: it's how long the *original* run's
    page took (render + model call + parse), which is the number worth showing; this
    replay's own render time is a different, much smaller thing.

    Unlike detect() (where rendering is sequential and only the model call/parse/annotate
    step is threaded, since that step is what's actually slow there), every page here does
    real work — render *and* annotate — with nothing waiting on a network call, so all of
    it is threaded. A higher default thread count than detect()'s than makes sense too:
    this is local, CPU-bound work with no external rate limit to respect.
    """
    started = time.monotonic()
    call = functools.partial(_rebuild_one_page, pdf_bytes, render_px)
    worker_count = min(threads, len(pages_meta)) or 1
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(call, meta) for meta in pages_meta]
        results = [future.result() for future in as_completed(futures)]
    logger.info(
        "rebuilt %d page(s) from history in %.3fs (%d threads)",
        len(results),
        time.monotonic() - started,
        worker_count,
    )
    return sorted(results, key=lambda result: result.page)
