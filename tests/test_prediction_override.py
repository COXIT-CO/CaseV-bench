"""Editable prediction JSON — the persisted manual override that never affects the Score
(ADR 0020, ticket 07). Exercises the ``PredictionOverrideService`` seam directly: setting an
override validates + persists without touching the model's original output, flips the overlay
render to the edited boxes, leaves the Score equal to the original; invalid input is rejected
with nothing persisted; revert restores the original; counting Predictions reject the edit.
"""

import pytest
from conftest import seed_page_images
from sqlmodel import Session, select

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.prompt import Prompt, Task
from core.models.results import BoundingBox, LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from core.services.prediction_override import (
    PredictionOverrideError,
    PredictionOverrideService,
)
from core.services.scoring import ScoringService

CAB = "cabinet"
COUNTER = "countertop"


def _detection(x_min, y_min, x_max, y_max, label=CAB) -> LocationDetection:
    return LocationDetection(
        label=label,
        bounding_box=BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
    )


def _location_result(engine, tmp_path, *, with_gt: bool = True) -> int:
    """A single-page location Result whose page image lives where render-on-demand expects it,
    so the edited-overlay render exercises a real render. Returns the Result id."""
    drawing_dir = tmp_path / "drawing"
    seed_page_images(drawing_dir, 1)
    with Session(engine) as session:
        drawing = Drawing(name="floorplan")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        page = Page(
            drawing_id=drawing.id,
            page_number=1,
            image_path=str(drawing_dir / "page_0001.png"),
            width_px=64,
            height_px=64,
        )
        session.add(page)
        session.commit()
        session.refresh(page)

        if with_gt:
            session.add(
                LocationGroundTruth(
                    page_id=page.id,
                    label=CAB,
                    x_min=0.0,
                    y_min=0.0,
                    x_max=0.5,
                    y_max=0.5,
                )
            )
            session.commit()

        prompt = Prompt(task=Task.location, family="boxes", version=1, text="find")
        session.add(prompt)
        session.commit()
        session.refresh(prompt)
        run = Run(
            task=Task.location,
            prompt_id=prompt.id,
            drawing_id=drawing.id,
            status=RunStatus.done,
            progress=1,
            total_units=1,
            dpi=150,
            downsample_px=1568,
            max_tokens=1024,
            temperature=0.0,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        result = Result(run_id=run.id, model="anthropic/claude-sonnet-4.5")
        session.add(result)
        session.commit()
        session.refresh(result)

        original = LocationResult(detections=[_detection(0.0, 0.0, 0.5, 0.5)])
        session.add(
            Prediction(
                result_id=result.id,
                page_id=page.id,
                page_number=1,
                status=PredictionStatus.ok,
                raw_content=original.model_dump_json(),
                parsed_json=original.model_dump_json(),
                overlay_path=None,
            )
        )
        session.commit()
        return result.id


def _counting_result(engine) -> int:
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
        prompt = Prompt(task=Task.counting, family="strict", version=1, text="count")
        session.add(prompt)
        session.commit()
        session.refresh(prompt)
        run = Run(
            task=Task.counting,
            prompt_id=prompt.id,
            drawing_id=drawing.id,
            status=RunStatus.done,
            progress=1,
            total_units=1,
            dpi=150,
            downsample_px=1568,
            max_tokens=1024,
            temperature=0.0,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        result = Result(run_id=run.id, model="m")
        session.add(result)
        session.commit()
        session.refresh(result)
        session.add(
            Prediction(
                result_id=result.id,
                page_id=page.id,
                page_number=1,
                status=PredictionStatus.ok,
                raw_content='{"cabinet": 1, "countertop": 0, "elevation": 0, "elevation_callout": 0}',
                parsed_json='{"cabinet": 1, "countertop": 0, "elevation": 0, "elevation_callout": 0}',
            )
        )
        session.commit()
        return result.id


def test_set_override_persists_and_leaves_original_intact(engine, tmp_path):
    result_id = _location_result(engine, tmp_path)
    edited = LocationResult(
        detections=[_detection(0.1, 0.1, 0.9, 0.9)]
    ).model_dump_json()

    with Session(engine) as session:
        pred = PredictionOverrideService(session).set_override(result_id, 1, edited)
        assert pred.edited_json is not None
        # The model's original output is never mutated.
        original = LocationResult(detections=[_detection(0.0, 0.0, 0.5, 0.5)])
        assert pred.parsed_json == original.model_dump_json()
        assert pred.raw_content == original.model_dump_json()
        # The edit is the validated, normalized LocationResult JSON.
        assert (
            LocationResult.model_validate_json(pred.edited_json)
            .detections[0]
            .bounding_box.x_min
            == 0.1
        )


def test_edited_overlay_renders_from_the_edit_and_reverts(engine, tmp_path):
    result_id = _location_result(engine, tmp_path)
    edited = LocationResult(
        detections=[_detection(0.1, 0.1, 0.9, 0.9)]
    ).model_dump_json()

    with Session(engine) as session:
        svc = PredictionOverrideService(session)
        # No edit yet → no on-demand overlay (the cached run-time overlay is served instead).
        assert svc.edited_overlay_png(result_id, 1) is None

        svc.set_override(result_id, 1, edited)
        png = svc.edited_overlay_png(result_id, 1)
        assert png is not None and png.startswith(b"\x89PNG")

        # Revert clears the edit and the on-demand render falls away again.
        pred = svc.revert(result_id, 1)
        assert pred.edited_json is None
        assert svc.edited_overlay_png(result_id, 1) is None


def test_edit_never_changes_the_score(engine, tmp_path):
    result_id = _location_result(engine, tmp_path, with_gt=True)
    with Session(engine) as session:
        before = ScoringService(session).score_location_result(result_id)
        before_f1 = before.f1

    # Edit the boxes to something that would score very differently if it counted.
    bogus = LocationResult(
        detections=[_detection(0.0, 0.0, 0.01, 0.01, label=COUNTER)]
    ).model_dump_json()
    with Session(engine) as session:
        PredictionOverrideService(session).set_override(result_id, 1, bogus)
    with Session(engine) as session:
        after = ScoringService(session).score_location_result(result_id)
        assert after.f1 == before_f1


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        '{"detections": [{"label": "walls", "bounding_box": {"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1}}]}',
        '{"detections": [{"label": "cabinet", "bounding_box": {"x_min": 0, "y_min": 0, "x_max": 1.5, "y_max": 1}}]}',
    ],
    ids=["invalid-json", "bad-label", "coord-out-of-range"],
)
def test_invalid_override_is_rejected_and_nothing_persisted(engine, tmp_path, payload):
    result_id = _location_result(engine, tmp_path)
    with Session(engine) as session:
        with pytest.raises(PredictionOverrideError):
            PredictionOverrideService(session).set_override(result_id, 1, payload)
    # Nothing was persisted — the Prediction stays unedited.
    with Session(engine) as session:
        pred = session.exec(
            select(Prediction).where(Prediction.result_id == result_id)
        ).first()
        assert pred.edited_json is None


def test_counting_prediction_rejects_the_edit(engine):
    result_id = _counting_result(engine)
    edited = LocationResult(
        detections=[_detection(0.1, 0.1, 0.2, 0.2)]
    ).model_dump_json()
    with Session(engine) as session:
        with pytest.raises(PredictionOverrideError):
            PredictionOverrideService(session).set_override(result_id, 1, edited)


def test_missing_prediction_raises_lookup_error(engine, tmp_path):
    result_id = _location_result(engine, tmp_path)
    with Session(engine) as session:
        with pytest.raises(LookupError):
            PredictionOverrideService(session).set_override(result_id, 99, "{}")
