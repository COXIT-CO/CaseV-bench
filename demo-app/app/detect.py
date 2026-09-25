"""Renders every page of an uploaded PDF, sends each to a model, and draws the
predicted boxes onto the page. No dataset, no ground truth, no scoring, nothing
written to disk beyond what pymupdf/Pillow need in memory."""

from __future__ import annotations

import functools
import io
import logging
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
from core.render import PageRenderer, RenderedPage
from core.scoring import Box
from location_overlay import render as render_overlay

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 20.0

PROMPT_TEXT = PROMPT_PATH.read_text()

_PendingPage = tuple[int, RenderedPage]
ProgressCallback = Callable[[str], None]

_NOOP_PROGRESS: ProgressCallback = lambda _message: None  # noqa: E731

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
    annotated_png: bytes
    boxes: list[Box]
    dropped: int
    complete: bool
    status: str  # "ok" | "failed"
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


def _annotate(original_png: bytes, boxes: list[Box]) -> bytes:
    if not boxes:
        return original_png
    with Image.open(io.BytesIO(original_png)) as page_image:
        annotated = render_overlay(page_image, boxes)
    buffer = io.BytesIO()
    annotated.save(buffer, format="PNG")
    return buffer.getvalue()


def _process_page(
    item: _PendingPage,
    *,
    client: ModelClient,
    model: str,
    prompt: str,
    params: GenerationParams,
    max_attempts: int,
    total_pages: int,
    on_progress: ProgressCallback,
) -> PageResult:
    page_number, rendered = item
    page_started = time.monotonic()
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
        return PageResult(
            page=page_number,
            width=rendered.width,
            height=rendered.height,
            original_png=rendered.png_bytes,
            annotated_png=rendered.png_bytes,
            boxes=[],
            dropped=0,
            complete=False,
            status="failed",
            error=str(exc),
        )

    on_progress(f"Page {page_number} of {total_pages}: response received, parsing…")
    parse_started = time.monotonic()
    parsed = ResponseParser().parse_response(response.text, page_number)
    parse_elapsed = time.monotonic() - parse_started

    annotate_started = time.monotonic()
    annotated_png = _annotate(rendered.png_bytes, parsed.boxes)
    annotate_elapsed = time.monotonic() - annotate_started

    page_elapsed = time.monotonic() - page_started
    logger.info(
        "page %d/%d: done in %.2fs total (parse=%.3fs annotate=%.3fs, %d box(es), %d dropped)",
        page_number,
        total_pages,
        page_elapsed,
        parse_elapsed,
        annotate_elapsed,
        len(parsed.boxes),
        parsed.dropped,
    )
    on_progress(
        f"Page {page_number} of {total_pages}: done — {len(parsed.boxes)} object(s) found "
        f"({page_elapsed:.1f}s)"
    )
    return PageResult(
        page=page_number,
        width=rendered.width,
        height=rendered.height,
        original_png=rendered.png_bytes,
        annotated_png=annotated_png,
        boxes=parsed.boxes,
        dropped=parsed.dropped,
        complete=parsed.complete,
        status="ok",
        error=None,
    )


def detect(
    pdf_bytes: bytes,
    *,
    client: ModelClient,
    model: str,
    max_px: int,
    prompt: str = PROMPT_TEXT,
    threads: int = 4,
    on_progress: ProgressCallback | None = None,
) -> list[PageResult]:
    progress = on_progress or _NOOP_PROGRESS
    run_started = time.monotonic()

    progress("Opening PDF…")
    open_started = time.monotonic()
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        logger.info("opened PDF (%d bytes) in %.3fs", len(pdf_bytes), time.monotonic() - open_started)
        page_count = document.page_count
        if page_count == 0:
            raise ValueError("the PDF has no pages")

        pending: list[_PendingPage] = []
        render_started = time.monotonic()
        for page_number in range(1, page_count + 1):
            progress(f"Rendering page {page_number} of {page_count}…")
            page_render_started = time.monotonic()
            rendered = PageRenderer.render_page(document, page_number, max_px)
            logger.info(
                "page %d/%d: rendered in %.3fs (%dx%d, %.0f dpi)",
                page_number,
                page_count,
                time.monotonic() - page_render_started,
                rendered.width,
                rendered.height,
                rendered.effective_dpi,
            )
            pending.append((page_number, rendered))
        logger.info("rendered all %d page(s) in %.3fs", page_count, time.monotonic() - render_started)

    progress(f"Sending {page_count} page(s) to {model}…")
    call = functools.partial(
        _process_page,
        client=client,
        model=model,
        prompt=prompt,
        params=GenerationParams(DEFAULT_TEMPERATURE, DEFAULT_MAX_OUTPUT_TOKENS),
        max_attempts=MAX_ATTEMPTS,
        total_pages=page_count,
        on_progress=progress,
    )
    dispatch_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(threads, len(pending))) as executor:
        futures = [executor.submit(call, item) for item in pending]
        results = [future.result() for future in as_completed(futures)]
    logger.info(
        "all %d page(s) processed in %.3fs wall clock (%d threads)",
        page_count,
        time.monotonic() - dispatch_started,
        min(threads, len(pending)),
    )

    total_elapsed = time.monotonic() - run_started
    logger.info("detect() total: %.3fs for %d page(s)", total_elapsed, page_count)
    progress(f"Finishing up… (total {total_elapsed:.1f}s)")
    return sorted(results, key=lambda result: result.page)
