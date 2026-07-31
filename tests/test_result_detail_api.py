"""JSON contract for the Result drill-down (spec §A.3, ticket 03). ``GET /api/results/{id}``
is the twin of the retired Jinja ``/results/{id}`` page: the header refs, the Score with
per-label detail, and the per-page Predictions
(raw content + parsed JSON, or a parse-error failure record). The prediction overlay PNG is
re-mounted under ``/api`` for the SPA.

Results are seeded directly so the score is exact without depending on a live model.
"""

from PIL import Image
from sqlmodel import Session

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.prompt import Prompt, Task
from core.models.results import BoundingBox, LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus

CAB = "cabinet"
ACCURATE = "anthropic/claude-sonnet-4.5"


def _detection(x_min, y_min, x_max, y_max, label=CAB) -> LocationDetection:
    return LocationDetection(
        label=label,
        bounding_box=BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
    )


def _seed_location_result(engine, tmp_path, *, with_gt: bool) -> int:
    """A location Result: page 1 predicts two boxes (one matches GT, one spurious) with a
    missed GT box; page 2 predicts one box with no GT on that page. Returns the Result id.
    """
    with Session(engine) as session:
        drawing = Drawing(name="floorplan")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)

        pages = []
        for n in (1, 2):
            image_path = tmp_path / f"page_{n}.png"
            Image.new("RGB", (100, 100), "white").save(image_path)
            page = Page(
                drawing_id=drawing.id,
                page_number=n,
                image_path=str(image_path),
                width_px=100,
                height_px=100,
            )
            session.add(page)
            pages.append(page)
        session.commit()
        for page in pages:
            session.refresh(page)

        if with_gt:
            # GT only on page 1: one box the prediction matches, one it misses.
            session.add(
                LocationGroundTruth(
                    page_id=pages[0].id,
                    label=CAB,
                    x_min=0.0,
                    y_min=0.0,
                    x_max=0.5,
                    y_max=0.5,
                )
            )
            session.add(
                LocationGroundTruth(
                    page_id=pages[0].id,
                    label=CAB,
                    x_min=0.8,
                    y_min=0.8,
                    x_max=1.0,
                    y_max=1.0,
                )
            )
            session.commit()

        prompt = Prompt(
            task=Task.location, family="boxes", version=2, text="find boxes"
        )
        session.add(prompt)
        session.commit()
        session.refresh(prompt)

        run = _done_run(session, Task.location, prompt.id, drawing.id)
        result = Result(run_id=run.id, model=ACCURATE)
        session.add(result)
        session.commit()
        session.refresh(result)

        page1 = LocationResult(
            detections=[_detection(0.0, 0.0, 0.5, 0.5), _detection(0.6, 0.0, 0.7, 0.1)]
        )
        session.add(
            Prediction(
                result_id=result.id,
                page_id=pages[0].id,
                page_number=1,
                status=PredictionStatus.ok,
                raw_content=page1.model_dump_json(),
                parsed_json=page1.model_dump_json(),
            )
        )
        page2 = LocationResult(detections=[_detection(0.1, 0.1, 0.2, 0.2)])
        session.add(
            Prediction(
                result_id=result.id,
                page_id=pages[1].id,
                page_number=2,
                status=PredictionStatus.ok,
                raw_content=page2.model_dump_json(),
                parsed_json=page2.model_dump_json(),
            )
        )
        session.commit()
        return result.id


def _done_run(session, task, prompt_id, drawing_id) -> Run:
    run = Run(
        task=task,
        prompt_id=prompt_id,
        drawing_id=drawing_id,
        status=RunStatus.done,
        progress=1,
        total_units=1,
        dpi=200,
        downsample_px=1568,
        max_tokens=1024,
        temperature=0.0,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_location_result_detail_returns_rates_and_box_counts(client, engine, tmp_path):
    result_id = _seed_location_result(engine, tmp_path, with_gt=True)

    body = client.get(f"/api/results/{result_id}").json()

    # The header refs the drill-down puts above the score.
    assert body["result_id"] == result_id
    assert body["task"] == "location"
    assert body["model"] == ACCURATE
    assert body["prompt_family"] == "boxes"
    assert body["prompt_version"] == 2
    assert body["drawing_name"] == "floorplan"
    assert body["scored"] is True
    assert body["label_count"] == 4

    # The Result view shows the same per-run knobs snapshot the Run recorded (ticket 04).
    assert body["knobs"] == {
        "dpi": 200,
        "downsample_px": 1568,
        "max_tokens": 1024,
        "temperature": 0.0,
    }

    # Location score shape: micro-averaged P/R/F1 + per-label tp/fp/fn/rates.
    score = body["location_score"]
    cab = next(ls for ls in score["per_label"] if ls["label"] == "cabinet")
    assert cab["tp"] == 1  # one predicted box matches a GT box
    assert cab["fp"] == 2  # one spurious box on p1, one predicted box on the GT-less p2
    assert cab["fn"] == 1  # one GT box never covered
    assert score["precision"] == cab["precision"]

    # Each page carries its predicted box count (the GT visuals are gone; ticket 01).
    preds = {p["page_number"]: p for p in body["predictions"]}
    assert preds[1]["box_count"] == 2
    assert preds[2]["box_count"] == 1
    # The dropped compare-overlay flag is no longer part of the payload.
    assert "has_gt" not in preds[1]


def test_salvaged_location_error_surfaces_boxes_and_json(client, engine, tmp_path):
    """A salvaged-but-not-clean location Prediction stays ``error`` yet the drill-down still
    reports its stored boxes and parsed JSON (ADR 0019, ticket 03) — not a bare failure.
    """
    with Session(engine) as session:
        drawing = Drawing(name="kitchen")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        page = Page(
            drawing_id=drawing.id,
            page_number=1,
            image_path="/tmp/page.png",
            width_px=100,
            height_px=100,
        )
        session.add(page)
        session.commit()
        session.refresh(page)
        prompt = Prompt(
            task=Task.location, family="boxes", version=1, text="find boxes"
        )
        session.add(prompt)
        session.commit()
        session.refresh(prompt)
        run = _done_run(session, Task.location, prompt.id, drawing.id)
        result = Result(run_id=run.id, model=ACCURATE)
        session.add(result)
        session.commit()
        session.refresh(result)
        salvaged = LocationResult(detections=[_detection(0.1, 0.1, 0.2, 0.2)])
        session.add(
            Prediction(
                result_id=result.id,
                page_id=page.id,
                page_number=1,
                status=PredictionStatus.error,
                raw_content='[{"label": "cabinet", ... truncated',
                parsed_json=salvaged.model_dump_json(),
                parse_error="response was truncated; salvaged intact array elements",
            )
        )
        session.commit()
        result_id = result.id

    body = client.get(f"/api/results/{result_id}").json()
    (pred,) = body["predictions"]
    assert pred["status"] == "error"
    # The salvaged box is counted and its JSON passed through for display.
    assert pred["box_count"] == 1
    assert pred["parsed_json"] is not None
    assert pred["parse_error"]


def test_location_result_unscored_without_ground_truth(client, engine, tmp_path):
    result_id = _seed_location_result(engine, tmp_path, with_gt=False)

    body = client.get(f"/api/results/{result_id}").json()
    assert body["scored"] is False
    assert body["location_score"] is None
    # Predictions are still returned so the model's boxes stay inspectable.
    assert len(body["predictions"]) == 2


def test_result_detail_missing_result_404s(client):
    missing = client.get("/api/results/999")
    assert missing.status_code == 404


def test_api_prediction_overlay_served_and_compare_overlay_gone(
    client, engine, tmp_path
):
    result_id = _seed_location_result(engine, tmp_path, with_gt=True)

    # The dropped GT-vs-prediction compare route no longer resolves (ticket 01).
    compare = client.get(f"/api/results/{result_id}/pages/1/compare-overlay")
    assert compare.status_code == 404

    # A page with a prediction but no cached prediction-overlay 404s (no overlay_path set).
    overlay = client.get(f"/api/results/{result_id}/pages/1/overlay")
    assert overlay.status_code == 404
