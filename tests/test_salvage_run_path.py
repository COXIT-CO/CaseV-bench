"""Salvage outcomes through the run seam (ADR 0019, ticket 03).

Drives the same ``OpenRouterAdapter`` stub as the scope-1 run tests to assert the
*external* behaviour of the robust parser: which crafted response yields a clean ``ok``
vs. a non-clean ``error`` whose best-effort salvage is still stored, drawn, and (since ADR
0027) scored. Never asserts internal parser mechanics — only the ``Prediction`` a Run
produces.
"""

import json
from pathlib import Path

from conftest import seed_page_images

from core.models.drawing import Drawing, Page
from core.models.prompt import Task
from core.models.results import LocationResult
from core.models.run import PredictionStatus, RunStatus
from core.services.prompt import PromptService
from core.services.run import RunService

MODEL = "anthropic/claude-sonnet-4.5"

BOXES = [
    {
        "label": "cabinet",
        "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4},
    },
    {
        "label": "countertop",
        "bounding_box": {"x_min": 0.5, "y_min": 0.5, "x_max": 0.6, "y_max": 0.7},
    },
]
CLEAN_BOXES = json.dumps(BOXES)


def _seed_location(session, tmp_path):
    drawing = Drawing(name="sample")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    (image_path,) = seed_page_images(tmp_path / str(drawing.id), n_pages=1)
    session.add(
        Page(
            drawing_id=drawing.id,
            page_number=1,
            image_path=str(image_path),
            width_px=100,
            height_px=100,
        )
    )
    session.commit()
    session.refresh(drawing)
    prompt = PromptService(session).create(Task.location, "default", "find them")
    return drawing, prompt


def _only_prediction(run):
    (result,) = run.results
    (pred,) = result.predictions
    return pred


def _launch(session, stub_adapter, tmp_path, content):
    drawing, prompt = _seed_location(session, tmp_path)
    stub_adapter.responses = {MODEL: content}
    run = RunService(session, stub_adapter, overlay_root=tmp_path / "overlays").launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )
    assert run.status == RunStatus.done
    return _only_prediction(run)


def _labels(pred):
    return [
        d.label for d in LocationResult.model_validate_json(pred.parsed_json).detections
    ]


def test_prose_wrapped_boxes_are_scored_ok(session, stub_adapter, tmp_path):
    # Chatty prose around an otherwise-valid array is tolerated → a scored ok.
    content = f"Sure, here are the boxes:\n```json\n{CLEAN_BOXES}\n```\nLet me know!"
    pred = _launch(session, stub_adapter, tmp_path, content)
    assert pred.status == PredictionStatus.ok
    assert _labels(pred) == ["cabinet", "countertop"]
    assert pred.parse_error is None


def test_trailing_comma_and_single_quotes_are_scored_ok(
    session, stub_adapter, tmp_path
):
    content = CLEAN_BOXES.replace('"', "'").rstrip("]") + ",]"
    pred = _launch(session, stub_adapter, tmp_path, content)
    assert pred.status == PredictionStatus.ok
    assert _labels(pred) == ["cabinet", "countertop"]


def test_truncated_location_array_salvages_boxes_and_renders_overlay(
    session, stub_adapter, tmp_path
):
    drawing, prompt = _seed_location(session, tmp_path)
    # One intact box, then the array is cut off mid-second element.
    truncated = "[" + json.dumps(BOXES[0]) + ', {"label": "countertop", "bounding_box'
    stub_adapter.responses = {MODEL: truncated}
    overlay_root = tmp_path / "overlays"

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )
    pred = _only_prediction(run)

    # The non-clean parse stays an ``error`` (the clean/salvaged flag survives as a
    # displayed data-quality badge, ADR 0027)…
    assert pred.status == PredictionStatus.error
    assert pred.parse_error
    # …and the box that did parse is stored (and now scored, ADR 0027) with an overlay drawn.
    parsed = LocationResult.model_validate_json(pred.parsed_json)
    assert [d.label for d in parsed.detections] == ["cabinet"]
    assert pred.overlay_path is not None
    assert Path(pred.overlay_path).exists()
    # The raw model output is always retained for inspection.
    assert pred.raw_content == truncated


def test_empty_location_array_is_scored_ok_with_no_boxes(
    session, stub_adapter, tmp_path
):
    """An empty array is a valid "no objects on this page" answer, not a failure: it scores
    a clean ok with zero detections, never an error. Even with no boxes to draw an overlay is
    still rendered — the bare page image — so the UI shows the page a clean ``ok`` promises
    rather than a broken image."""
    drawing, prompt = _seed_location(session, tmp_path)
    stub_adapter.responses = {MODEL: "[]"}
    overlay_root = tmp_path / "overlays"

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )
    pred = _only_prediction(run)

    assert pred.status == PredictionStatus.ok
    assert pred.parse_error is None
    parsed = LocationResult.model_validate_json(pred.parsed_json)
    assert parsed.detections == []
    # No boxes, but a clean ok still renders the bare page so the UI has an image to show.
    assert pred.overlay_path is not None
    assert Path(pred.overlay_path).exists()
    assert pred.raw_content == "[]"


def test_total_garbage_location_is_error_with_no_boxes(session, stub_adapter, tmp_path):
    drawing, prompt = _seed_location(session, tmp_path)
    stub_adapter.responses = {MODEL: "the drawing was unreadable, sorry"}
    overlay_root = tmp_path / "overlays"

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )
    pred = _only_prediction(run)

    assert pred.status == PredictionStatus.error
    assert pred.parsed_json is None
    assert pred.overlay_path is None


def test_bad_first_parse_then_clean_retry_is_scored_ok(session, stub_adapter, tmp_path):
    drawing, prompt = _seed_location(session, tmp_path)
    # First attempt is unsalvageable garbage; the single retry returns clean JSON.
    replies = iter(["total nonsense, no json here", CLEAN_BOXES])

    def send(image_path, model, prompt, **kwargs):
        content = next(replies)
        return {"choices": [{"message": {"content": content}}]}

    stub_adapter.send_image_prompt = send

    run = RunService(session, stub_adapter, overlay_root=tmp_path / "overlays").launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )
    pred = _only_prediction(run)

    assert pred.status == PredictionStatus.ok
    assert pred.parse_error is None
    assert _labels(pred) == ["cabinet", "countertop"]


def test_salvage_survives_a_raising_retry(session, stub_adapter, tmp_path):
    """When the first attempt salvages boxes (non-clean) and the retry then raises, the
    first attempt's salvage is kept — a raised retry never discards it (ADR 0019)."""
    drawing, prompt = _seed_location(session, tmp_path)
    truncated = "[" + json.dumps(BOXES[0]) + ', {"label": "countertop", "bounding_box'
    calls = iter([truncated])

    def send(image_path, model, prompt, **kwargs):
        # First call salvages one box; the retry raises a transient network error.
        try:
            content = next(calls)
        except StopIteration:
            raise RuntimeError("boom: openrouter unreachable")
        return {"choices": [{"message": {"content": content}}]}

    stub_adapter.send_image_prompt = send
    overlay_root = tmp_path / "overlays"

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )
    pred = _only_prediction(run)

    assert pred.status == PredictionStatus.error
    # The salvaged box from the first attempt is retained, not clobbered by the raised retry.
    parsed = LocationResult.model_validate_json(pred.parsed_json)
    assert [d.label for d in parsed.detections] == ["cabinet"]
    assert pred.raw_content == truncated
    assert pred.overlay_path is not None


def test_clean_first_parse_does_not_retry(session, stub_adapter, tmp_path):
    drawing, prompt = _seed_location(session, tmp_path)
    stub_adapter.responses = {MODEL: CLEAN_BOXES}
    RunService(session, stub_adapter, overlay_root=tmp_path / "overlays").launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )
    # A clean parse short-circuits the retry: exactly one adapter call.
    assert len([c for c in stub_adapter.calls if c["model"] == MODEL]) == 1
