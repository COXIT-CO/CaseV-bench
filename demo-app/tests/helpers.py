"""Shared fixtures for demo-app's test suite: no network, no real model calls, no
real Postgres. Mirrors the pattern src/runner/tests/helpers.py already uses."""

from __future__ import annotations

from collections.abc import Callable

import pymupdf

from core.client import GenerationParams, ModelClientError, ModelResponse, Usage

# One box, well within a page's bounds — enough for tests that care about "a detection
# happened" without needing to assert on exact coordinates.
_DEFAULT_RESPONSE_TEXT = (
    '[{"label": "elevation", "bounding_box": '
    '{"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4}}]'
)


def ok_response(text: str = _DEFAULT_RESPONSE_TEXT, *, cost_usd: float | None = 0.001) -> ModelResponse:
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
        self._respond = respond or ok_response
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


class FailingModelClient:
    """A ModelClient whose every call raises, for retry/failure-path tests."""

    def __init__(self, message: str = "boom") -> None:
        self.message = message
        self.calls = 0

    def generate(self, **_: object) -> ModelResponse:
        self.calls += 1
        raise ModelClientError(self.message)


def make_pdf_bytes(page_count: int = 1, *, width: float = 612.0, height: float = 792.0) -> bytes:
    """A minimal blank multi-page PDF — enough for detect()/rebuild_pages() to render,
    with no real drawing content (nothing for a stub model to "find")."""
    document = pymupdf.open()
    for _ in range(page_count):
        document.new_page(width=width, height=height)
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes
