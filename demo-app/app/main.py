from __future__ import annotations

import asyncio
import base64
import json
import queue
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.detect import PageResult, detect
from core.client import ApiKeyError, OpenRouterClient
from core.config import DEFAULT_MAX_PX, MODEL_ROSTER, PROMPT_PATH
from core.dataset import ALLOWED_LABELS

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

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

app = FastAPI(title="CaseV-Bench demo")


def _data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _client() -> OpenRouterClient:
    try:
        return OpenRouterClient.from_env()
    except ApiKeyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _result_payload(
    model: str, target_max_px: int, provider_cap: int | None, results: list[PageResult]
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
        "pages": pages,
        "detections": detections,
    }


async def _detect_stream(
    pdf_bytes: bytes,
    *,
    client: OpenRouterClient,
    model: str,
    target_max_px: int,
    provider_cap: int | None,
) -> AsyncIterator[bytes]:
    events: queue.Queue = queue.Queue()
    outcome: dict[str, object] = {}

    def on_progress(message: str) -> None:
        events.put({"type": "progress", "message": message})

    def run() -> None:
        try:
            outcome["results"] = detect(
                pdf_bytes,
                client=client,
                model=model,
                max_px=target_max_px,
                on_progress=on_progress,
            )
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

    if "error" in outcome:
        yield (json.dumps({"type": "error", "message": outcome["error"]}) + "\n").encode()
        return

    payload = _result_payload(model, target_max_px, provider_cap, outcome["results"])
    yield (json.dumps({"type": "result", "data": payload}) + "\n").encode()


@app.get("/api/config")
def get_config() -> dict[str, object]:
    return {
        "models": MODEL_ROSTER,
        "labels": LABEL_ORDER,
        "prompt": PROMPT_PATH.read_text(),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }


@app.post("/api/detect")
async def run_detect(
    file: UploadFile = File(...),
    model: str = Form(...),
) -> StreamingResponse:
    model = model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")

    is_pdf = file.content_type in ("application/pdf", "application/x-pdf") or (
        file.filename or ""
    ).lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(status_code=400, detail="upload must be a PDF")

    pdf_bytes = await file.read()
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
            target_max_px=target_max_px,
            provider_cap=provider_cap,
        ),
        media_type="application/x-ndjson",
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
