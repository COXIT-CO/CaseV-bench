"""Location-run execution test (ticket 09 — the primary seam).

With the OpenRouter adapter stubbed to canned bounding-box responses, launching a
location Run through the shared service against a temp SQLite DB parses each model's
output into labeled normalized boxes, persists them as location Predictions per
(Result, Page), and renders a prediction-overlay PNG on the page image. A response
that fails to parse twice is recorded as a failure Prediction — same one-retry-then-
record behavior as counting — without aborting the Run or rendering an overlay.
"""

import json
from pathlib import Path

from PIL import Image
from sqlmodel import select

from models.drawing import Drawing, Page
from models.prompt import Task
from models.results import LocationResult
from models.run import Prediction, PredictionStatus, Result, RunStatus
from services.prompt import PromptService
from services.run import RunService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"

BOXES_JSON = json.dumps(
    [
        {
            "label": "cabinets",
            "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4},
        },
        {
            "label": "countertops",
            "bounding_box": {"x_min": 0.5, "y_min": 0.5, "x_max": 0.6, "y_max": 0.7},
        },
    ]
)


def _seed_drawing(session, tmp_path: Path, n_pages: int) -> Drawing:
    """Seed a Drawing whose Pages point at real (small) PNGs so overlay rendering,
    which opens the page image, has something to draw on."""
    drawing = Drawing(name="sample")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    for page_number in range(1, n_pages + 1):
        image_path = tmp_path / f"page_{page_number}.png"
        Image.new("RGB", (100, 100), "white").save(image_path)
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=str(image_path),
                width_px=100,
                height_px=100,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def _seed_prompt(session):
    return PromptService(session).create(Task.location, "default", "find them")


def test_location_run_persists_predictions_and_overlays(
    session, stub_adapter, tmp_path
):
    drawing = _seed_drawing(session, tmp_path, n_pages=2)
    prompt = _seed_prompt(session)
    stub_adapter.responses = {SONNET: BOXES_JSON, GPT: BOXES_JSON}
    overlay_root = tmp_path / "overlays"

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [SONNET, GPT]
    )

    assert run.status == RunStatus.done
    assert run.progress == run.total_units == 4  # 2 models * 2 pages

    results = session.exec(select(Result).where(Result.run_id == run.id)).all()
    assert {r.model for r in results} == {SONNET, GPT}
    for result in results:
        preds = session.exec(
            select(Prediction)
            .where(Prediction.result_id == result.id)
            .order_by(Prediction.page_number)
        ).all()
        assert [p.page_number for p in preds] == [1, 2]
        for pred in preds:
            assert pred.status == PredictionStatus.ok
            assert pred.raw_content == BOXES_JSON
            # Parsed output is stored as a LocationResult (the detected boxes).
            parsed = LocationResult.model_validate_json(pred.parsed_json)
            assert [d.label for d in parsed.detections] == ["cabinets", "countertops"]
            assert parsed.detections[0].bounding_box.x_min == 0.1
            # A prediction-overlay PNG was rendered on the page image.
            assert pred.overlay_path is not None
            assert Path(pred.overlay_path).exists()


def test_location_parse_failure_is_recorded_without_overlay(
    session, stub_adapter, tmp_path
):
    drawing = _seed_drawing(session, tmp_path, n_pages=1)
    prompt = _seed_prompt(session)
    stub_adapter.responses = {SONNET: BOXES_JSON, GPT: "not json at all"}
    overlay_root = tmp_path / "overlays"

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [SONNET, GPT]
    )

    # A model failure does not abort the Run.
    assert run.status == RunStatus.done
    assert run.progress == 2

    by_model = {r.model: r for r in run.results}
    good = by_model[SONNET].predictions
    assert [p.status for p in good] == [PredictionStatus.ok]
    assert good[0].overlay_path is not None

    bad = by_model[GPT].predictions
    assert len(bad) == 1
    assert bad[0].status == PredictionStatus.error
    assert bad[0].raw_content == "not json at all"
    assert bad[0].parse_error
    assert bad[0].parsed_json is None
    assert bad[0].overlay_path is None

    # The failing page was retried exactly once before being recorded (2 calls total).
    gpt_calls = [c for c in stub_adapter.calls if c["model"] == GPT]
    assert len(gpt_calls) == 2
