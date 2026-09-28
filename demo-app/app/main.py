from __future__ import annotations

import base64
import dataclasses
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles

from app import history, jobs
from app.detect import MAX_PAGES, PROMPT_TEXT, PageResult, detect, rebuild_pages
from core.client import ApiKeyError, OpenRouterClient
from core.config import DEFAULT_MAX_PX, MODEL_ROSTER
from core.dataset import ALLOWED_LABELS

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# History replay is for looking at a past run, not feeding it back to a model — render at a
# much smaller size than inference used (up to 5000px per the roster) to keep it fast and the
# response small. See get_run().
HISTORY_REPLAY_MAX_PX = 1800
# The runner's own default (600s) is right for an unattended benchmark CLI run; here a page
# sits behind a live HTTP connection someone's browser is waiting on, so a single stalled
# attempt eating minutes is a much bigger problem. Comfortably above the slowest *successful*
# call observed in practice (~100s) without preserving the 600s worst case across 3 retries.
DEMO_MODEL_TIMEOUT_SECONDS = 150.0


def _normalize_prompt(text: str) -> str:
    """Browsers CRLF-normalize textarea values during multipart form submission, and
    PROMPT_TEXT (a raw file read) keeps its own trailing newline — comparing either
    un-normalized against the other made every submission look "custom" even when the
    prompt was never touched. Compare (and fall back on) this normalized form instead."""
    return text.replace("\r\n", "\n").strip()


_DEFAULT_PROMPT_NORMALIZED = _normalize_prompt(PROMPT_TEXT)

# Fixed display order for the pivot table's rows — ALLOWED_LABELS is a set, so this is the one
# place that order is decided. Matches the prompt's own TYPE 1-5 numbering.
LABEL_ORDER = [
    label
    for label in ("floor_plan", "elevation", "cabinet", "countertop", "callout")
    if label in ALLOWED_LABELS
]

logger = logging.getLogger("casev.demo.api")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [timing] %(message)s", "%H:%M:%S"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    history.ensure_schema()
    yield


app = FastAPI(title="CaseV-Bench demo", lifespan=lifespan)


@app.middleware("http")
async def no_cache(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """StaticFiles doesn't set Cache-Control on its own — only Last-Modified/ETag — so
    without this, browsers are free to apply heuristic caching and can keep serving a
    stale index.html/app.js indefinitely, silently, even across a plain reload. That's
    caused real confusion here: code changes looked like they "weren't working" when the
    browser had just never asked the server again. `no-cache` still lets the browser keep
    a cached copy for a cheap conditional (304) revalidation — it just can no longer skip
    asking entirely."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


def _data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _client() -> OpenRouterClient:
    try:
        client = OpenRouterClient.from_env()
    except ApiKeyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return dataclasses.replace(client, timeout_seconds=DEMO_MODEL_TIMEOUT_SECONDS)


def _page_payload(result: PageResult) -> dict[str, object]:
    """The full, wire-ready form of one page: images as data URLs plus its own
    detections. Used both for streaming a page the moment it's ready (the live
    /api/detect path) and for history replay (/api/runs/{id}, which still returns
    everything in one response — a past run's page count is already known and
    bounded by what completed originally, so accumulating there doesn't have the
    same unbounded-memory risk a long-running live multi-page detect does)."""
    detections = []
    for box in result.boxes:
        x_min, y_min, x_max, y_max = box["bbox"]
        detections.append(
            {
                "page": result.page,
                "label": box["object_type"],
                "x_min": round(x_min, 4),
                "y_min": round(y_min, 4),
                "x_max": round(x_max, 4),
                "y_max": round(y_max, 4),
            }
        )
    return {
        "page": result.page,
        "width": result.width,
        "height": result.height,
        "original_image": _data_url(result.original_png),
        "annotated_image": _data_url(result.annotated_png),
        "status": result.status,
        "error": result.error,
        "dropped": result.dropped,
        "complete": result.complete,
        "detections": detections,
    }


def _result_payload(
    model: str,
    target_max_px: int,
    provider_cap: int | None,
    custom_prompt: bool,
    results: list[PageResult],
) -> dict[str, object]:
    pages = []
    detections = []
    for result in results:
        page_data = _page_payload(result)
        detections.extend(page_data.pop("detections"))  # type: ignore[arg-type]
        pages.append(page_data)

    return {
        "model": model,
        "render_px": target_max_px,
        "used_provider_cap": provider_cap is not None,
        "custom_prompt": custom_prompt,
        "pages": pages,
        "detections": detections,
    }


def _run_detect_job(
    job: jobs.Job,
    pdf_bytes: bytes,
    *,
    client: OpenRouterClient,
    model: str,
    prompt: str,
    filename: str,
    target_max_px: int,
    provider_cap: int | None,
) -> None:
    """Runs on a background thread, started fire-and-forget by the /api/detect endpoint
    — which has already returned the job id to the caller by the time this is even
    scheduled. Everything here reaches the frontend only through `job.push(...)`,
    picked up whenever the next poll happens; nothing here is waiting on any HTTP
    connection, which is the whole point (see jobs.py's docstring)."""
    request_started = time.monotonic()
    custom_prompt = _normalize_prompt(prompt) != _DEFAULT_PROMPT_NORMALIZED

    def on_progress(message: str) -> None:
        job.push("progress", message)

    def on_page(result: PageResult) -> None:
        # Called the instant one page finishes — pushing it out here (rather than
        # collecting it) is what keeps memory bounded once each poll evicts it. See
        # detect()'s docstring and jobs.Job.poll's.
        job.push("page", _page_payload(result))

    try:
        pages_meta = detect(
            pdf_bytes,
            client=client,
            model=model,
            max_px=target_max_px,
            prompt=prompt,
            on_progress=on_progress,
            on_page=on_page,
        )
    except ValueError as exc:
        logger.info(
            "request for %s failed in %.3fs: %s", model, time.monotonic() - request_started, exc
        )
        job.push("error", {"message": str(exc)})
        job.finish("error")
        return

    try:
        history.save_run(
            filename=filename,
            model=model,
            prompt=prompt,
            custom_prompt=custom_prompt,
            render_px=target_max_px,
            pdf_bytes=pdf_bytes,
            pages_meta=pages_meta,
        )
    except Exception:
        # History is a convenience, not a dependency of the live detect flow — a DB
        # hiccup here must never take down the actual result the user is waiting on.
        logger.exception("failed to save run history for %s", model)

    logger.info(
        "request for %s completed in %.3fs (server-side, excludes upload, custom_prompt=%s)",
        model,
        time.monotonic() - request_started,
        custom_prompt,
    )
    # Pages/detections already pushed out one at a time above — this closing event just
    # carries the run-level metadata the frontend merges them with.
    job.push(
        "result",
        {
            "model": model,
            "render_px": target_max_px,
            "used_provider_cap": provider_cap is not None,
            "custom_prompt": custom_prompt,
        },
    )
    job.finish("done")


@app.get("/api/config")
def get_config() -> dict[str, object]:
    return {
        "models": MODEL_ROSTER,
        "labels": LABEL_ORDER,
        "default_prompt": PROMPT_TEXT,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_pages": MAX_PAGES,
    }


@app.post("/api/detect")
async def run_detect(
    file: UploadFile = File(...),
    model: str = Form(...),
    prompt: str = Form(""),
) -> dict[str, str]:
    model = model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")

    is_pdf = file.content_type in ("application/pdf", "application/x-pdf") or (
        file.filename or ""
    ).lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(status_code=400, detail="upload must be a PDF")

    # A blank submission (cleared textarea) falls back to the default rather than sending an
    # empty prompt to the model — that would just be a wasted, confusing call.
    normalized_prompt = _normalize_prompt(prompt)
    prompt_text = normalized_prompt or PROMPT_TEXT

    upload_started = time.monotonic()
    pdf_bytes = await file.read()
    logger.info(
        "received %s (%.2f MB) in %.3fs, model=%s, custom_prompt=%s",
        file.filename,
        len(pdf_bytes) / (1024 * 1024),
        time.monotonic() - upload_started,
        model,
        normalized_prompt != _DEFAULT_PROMPT_NORMALIZED,
    )
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB demo limit",
        )

    client = _client()
    provider_cap = MODEL_ROSTER.get(model)
    target_max_px = provider_cap or DEFAULT_MAX_PX

    job = jobs.create()
    threading.Thread(
        target=_run_detect_job,
        args=(job, pdf_bytes),
        kwargs={
            "client": client,
            "model": model,
            "prompt": prompt_text,
            "filename": file.filename or "upload.pdf",
            "target_max_px": target_max_px,
            "provider_cap": provider_cap,
        },
        daemon=True,
    ).start()
    return {"job_id": job.id}


def _get_job(job_id: str) -> jobs.Job:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found (finished a while ago, or never existed)")
    return job


@app.get("/api/jobs/{job_id}")
def poll_job(job_id: str, after: int = 0) -> dict[str, object]:
    status, next_after, events = _get_job(job_id).poll(after)
    return {"status": status, "next_after": next_after, "events": events}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, bool]:
    jobs.delete(job_id)
    return {"deleted": True}


def _parse_run_id(run_id: str) -> str:
    try:
        return str(uuid.UUID(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="malformed run id") from exc


@app.get("/api/runs")
def list_runs() -> list[dict[str, object]]:
    return [
        {
            "id": run.id,
            "created_at": run.created_at.isoformat(),
            "filename": run.filename,
            "model": run.model,
            "custom_prompt": run.custom_prompt,
            "page_count": run.page_count,
            "detection_count": run.detection_count,
        }
        for run in history.list_runs()
    ]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    detail = history.get_run(_parse_run_id(run_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")

    # detail.render_px is whatever the model needed at inference time (up to the roster's
    # 5000px cap) — replaying history is just for looking at it in a browser, so there's no
    # reason to pay for that resolution again. Boxes are normalized 0-1 coordinates, so they
    # still land in the right place regardless of the size rendered at.
    replay_started = time.monotonic()
    replay_px = min(detail.render_px, HISTORY_REPLAY_MAX_PX)
    rebuilt = rebuild_pages(detail.pdf_bytes, replay_px, detail.pages_meta)
    payload = _result_payload(
        detail.model,
        detail.render_px,
        MODEL_ROSTER.get(detail.model),
        detail.custom_prompt,
        rebuilt,
    )
    payload["filename"] = detail.filename
    logger.info(
        "replayed run %s: %d page(s) at %dpx (down from %dpx) in %.3fs",
        run_id,
        len(rebuilt),
        replay_px,
        detail.render_px,
        time.monotonic() - replay_started,
    )
    return payload


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict[str, object]:
    deleted = history.delete_run(_parse_run_id(run_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="run not found")
    return {"deleted": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
