from __future__ import annotations

import asyncio
import base64
import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import history
from app.detect import PROMPT_TEXT, PageResult, detect, rebuild_pages
from core.client import ApiKeyError, OpenRouterClient
from core.config import DEFAULT_MAX_PX, MODEL_ROSTER
from core.dataset import ALLOWED_LABELS

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# History replay is for looking at a past run, not feeding it back to a model — render at a
# much smaller size than inference used (up to 5000px per the roster) to keep it fast and the
# response small. See get_run().
HISTORY_REPLAY_MAX_PX = 1800


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

# NDJSON line ending the progress stream: the sentinel a background thread pushes once
# `detect()` returns or raises, so the async generator knows to stop draining the queue.
_DONE = object()

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


def _data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _client() -> OpenRouterClient:
    try:
        return OpenRouterClient.from_env()
    except ApiKeyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        pages.append(
            {
                "page": result.page,
                "width": result.width,
                "height": result.height,
                "original_image": _data_url(result.original_png),
                "annotated_image": _data_url(result.annotated_png),
                "status": result.status,
                "error": result.error,
                "dropped": result.dropped,
                "complete": result.complete,
            }
        )
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
        "model": model,
        "render_px": target_max_px,
        "used_provider_cap": provider_cap is not None,
        "custom_prompt": custom_prompt,
        "pages": pages,
        "detections": detections,
    }


def _pages_meta(results: list[PageResult]) -> list[dict[str, object]]:
    """The small, storable summary of a run history.save_run persists — status and
    boxes per page, deliberately excluding the (large) rendered images. See
    detect.rebuild_pages for how this gets turned back into a full result."""
    return [
        {
            "page": result.page,
            "status": result.status,
            "error": result.error,
            "dropped": result.dropped,
            "complete": result.complete,
            "boxes": list(result.boxes),
        }
        for result in results
    ]


async def _detect_stream(
    pdf_bytes: bytes,
    *,
    client: OpenRouterClient,
    model: str,
    prompt: str,
    filename: str,
    target_max_px: int,
    provider_cap: int | None,
) -> AsyncIterator[bytes]:
    events: queue.Queue = queue.Queue()
    outcome: dict[str, object] = {}
    request_started = time.monotonic()
    custom_prompt = _normalize_prompt(prompt) != _DEFAULT_PROMPT_NORMALIZED

    def on_progress(message: str) -> None:
        events.put({"type": "progress", "message": message})

    def run() -> None:
        try:
            results = detect(
                pdf_bytes,
                client=client,
                model=model,
                max_px=target_max_px,
                prompt=prompt,
                on_progress=on_progress,
            )
            outcome["results"] = results
            try:
                history.save_run(
                    filename=filename,
                    model=model,
                    prompt=prompt,
                    custom_prompt=custom_prompt,
                    render_px=target_max_px,
                    pdf_bytes=pdf_bytes,
                    pages_meta=_pages_meta(results),
                )
            except Exception:
                # History is a convenience, not a dependency of the live detect flow — a
                # DB hiccup here must never take down the actual result the user is waiting on.
                logger.exception("failed to save run history for %s", model)
        except ValueError as exc:
            outcome["error"] = str(exc)
        finally:
            events.put(_DONE)

    threading.Thread(target=run, daemon=True).start()

    while True:
        event = await asyncio.to_thread(events.get)
        if event is _DONE:
            break
        yield (json.dumps(event) + "\n").encode()

    request_elapsed = time.monotonic() - request_started
    if "error" in outcome:
        logger.info("request for %s failed in %.3fs: %s", model, request_elapsed, outcome["error"])
        yield (json.dumps({"type": "error", "message": outcome["error"]}) + "\n").encode()
        return

    logger.info(
        "request for %s completed in %.3fs (server-side, excludes upload, custom_prompt=%s)",
        model,
        request_elapsed,
        custom_prompt,
    )
    payload = _result_payload(model, target_max_px, provider_cap, custom_prompt, outcome["results"])
    yield (json.dumps({"type": "result", "data": payload}) + "\n").encode()


@app.get("/api/config")
def get_config() -> dict[str, object]:
    return {
        "models": MODEL_ROSTER,
        "labels": LABEL_ORDER,
        "default_prompt": PROMPT_TEXT,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }


@app.post("/api/detect")
async def run_detect(
    file: UploadFile = File(...),
    model: str = Form(...),
    prompt: str = Form(""),
) -> StreamingResponse:
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

    return StreamingResponse(
        _detect_stream(
            pdf_bytes,
            client=client,
            model=model,
            prompt=prompt_text,
            filename=file.filename or "upload.pdf",
            target_max_px=target_max_px,
            provider_cap=provider_cap,
        ),
        media_type="application/x-ndjson",
    )


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
