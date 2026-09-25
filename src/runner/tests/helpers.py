"""Shared fixtures for the runner test suite: no network, no Docker."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

import pymupdf

from core.client import GenerationParams, ModelClientError, ModelResponse, Usage

_PAGE_WIDTH, _PAGE_HEIGHT = 792.0, 612.0


class FractionalBox(TypedDict):
    """A ground-truth object expressed as a fraction (0-1) of the page, independent of the
    fixed page size `write_local_dataset` renders at."""

    label: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


# Matches write_smoke_dataset's page-1 ground truth exactly, so a run against that dataset
# using this response scores a clean match rather than an incidental one.
_ELEVATION_BOX: FractionalBox = {
    "label": "elevation",
    "x_min": 0.1,
    "y_min": 0.1,
    "x_max": 0.4,
    "y_max": 0.4,
}
_CABINET_BOX: FractionalBox = {
    "label": "cabinet",
    "x_min": 0.5,
    "y_min": 0.5,
    "x_max": 0.7,
    "y_max": 0.7,
}


def _bounding_box(box: FractionalBox) -> dict[str, float]:
    return {
        "x_min": box["x_min"],
        "y_min": box["y_min"],
        "x_max": box["x_max"],
        "y_max": box["y_max"],
    }


SMOKE_PAGE_RESPONSE_TEXT = json.dumps(
    [
        {"label": _ELEVATION_BOX["label"], "bounding_box": _bounding_box(_ELEVATION_BOX)},
        {"label": _CABINET_BOX["label"], "bounding_box": _bounding_box(_CABINET_BOX)},
    ]
)


def ok_response(text: str, *, cost_usd: float | None = 0.001) -> ModelResponse:
    return ModelResponse(
        text=text,
        finish_reason="stop",
        usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=cost_usd),
        latency_seconds=0.01,
        temperature_sent=0.0,
    )


class StubModelClient:
    """A ModelClient that returns canned responses without any network access."""

    def __init__(self, respond: Callable[[], ModelResponse] | None = None) -> None:
        self._respond = respond or (lambda: ok_response(SMOKE_PAGE_RESPONSE_TEXT))
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        image_mime_type: str,
        params: GenerationParams,
    ) -> ModelResponse:
        self.calls.append({"model": model, "prompt": prompt, "params": params})
        return self._respond()


def write_local_dataset(root: Path, drawing: str, pages_objects: list[list[FractionalBox]]) -> None:
    """Build a minimal on-disk dataset: an N-page PDF plus a matching native
    `obj-location.json`"""
    document = pymupdf.open()
    for _ in pages_objects:
        document.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
    document.save(root / "drawing.pdf")
    document.close()

    objects = []
    for page_number, page_objects in enumerate(pages_objects, start=1):
        for index, obj in enumerate(page_objects):
            x_min, y_min, x_max, y_max = obj["x_min"], obj["y_min"], obj["x_max"], obj["y_max"]
            objects.append(
                {
                    "id": f"{obj['label']}-{page_number}-{index}",
                    "category": obj["label"],
                    "page": page_number,
                    "bbox": {
                        "x": x_min * _PAGE_WIDTH,
                        "y": y_min * _PAGE_HEIGHT,
                        "width": (x_max - x_min) * _PAGE_WIDTH,
                        "height": (y_max - y_min) * _PAGE_HEIGHT,
                    },
                }
            )
    location = {"project_id": drawing, "objects": objects}
    (root / "obj-location.json").write_text(json.dumps(location))


def write_smoke_dataset(root: Path) -> Path:
    """A single-drawing, single-page dataset matching SMOKE_PAGE_RESPONSE_TEXT exactly."""
    write_local_dataset(root, "drawing-1", pages_objects=[[_ELEVATION_BOX, _CABINET_BOX]])
    return root


class ConcurrencyTrackingModelClient:
    """A ModelClient that records the highest number of `generate` calls observed in flight at
    once, to prove pages are actually dispatched in parallel."""

    def __init__(
        self,
        respond: Callable[[], ModelResponse] | None = None,
        *,
        hold_seconds: float = 0.05,
    ) -> None:
        self._respond = respond or (lambda: ok_response(SMOKE_PAGE_RESPONSE_TEXT))
        self._hold_seconds = hold_seconds
        self._lock = threading.Lock()
        self._active = 0
        self.max_concurrent = 0
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        image_mime_type: str,
        params: GenerationParams,
    ) -> ModelResponse:
        with self._lock:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        time.sleep(self._hold_seconds)
        with self._lock:
            self._active -= 1
        self.calls.append({"model": model, "prompt": prompt, "params": params})
        return self._respond()


class FailingModelClient:
    """A ModelClient whose every call raises, for retry/failure-path tests."""

    def __init__(self, message: str = "boom") -> None:
        self.message = message
        self.calls = 0

    def generate(self, **_: object) -> ModelResponse:
        self.calls += 1
        raise ModelClientError(self.message)
