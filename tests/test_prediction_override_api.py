"""HTTP contract for editable prediction JSON (ADR 0020, ticket 07): ``PUT``/``DELETE``
``/api/results/{id}/pages/{n}/prediction`` set and revert a location Prediction's manual
override, the overlay route redraws from it, and the Score never moves. Invalid input is a
precise ``400`` with nothing persisted; a counting Prediction is a ``400`` (location-only);
an unknown (Result, page) is a ``404``.
"""

from conftest import seed_page_images
from sqlmodel import Session

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.prompt import Prompt, Task
from core.models.results import BoundingBox, LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus

CAB = "cabinet"


def _detection(x_min, y_min, x_max, y_max, label=CAB) -> LocationDetection:
    return LocationDetection(
        label=label,
        bounding_box=BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
    )


def _seed_location(engine, tmp_path) -> int:
    """A one-page, GT-backed location Result with a real page image on disk, so the edited
    overlay renders. The prediction matches the single GT box (a clean F1 of 1.0). Returns id.
    """
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
        session.add(
            LocationGroundTruth(
                page_id=page.id, label=CAB, x_min=0.0, y_min=0.0, x_max=0.5, y_max=0.5
            )
        )
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
            )
        )
        session.commit()
        return result.id


def _seed_counting(engine) -> int:
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


def _edited(*detections) -> str:
    return LocationResult(detections=list(detections)).model_dump_json()


def test_set_override_returns_edit_and_leaves_score_and_original(
    client, engine, tmp_path
):
    result_id = _seed_location(engine, tmp_path)
    before = client.get(f"/api/results/{result_id}").json()
    assert before["scored"] is True
    f1_before = before["location_score"]["f1"]

    # Edit to two boxes that would score differently if the edit counted.
    body = {
        "edited_json": _edited(
            _detection(0.1, 0.1, 0.2, 0.2), _detection(0.6, 0.6, 0.7, 0.7)
        )
    }
    put = client.put(f"/api/results/{result_id}/pages/1/prediction", json=body)
    assert put.status_code == 200
    out = put.json()
    assert out["edited_json"] is not None
    assert out["box_count"] == 2  # the edited count, not the original single box

    # The drill-down now carries the edit; the model's original parsed_json is untouched, and
    # the Score is unchanged (the Leaderboard still ranks the model, not the correction).
    detail = client.get(f"/api/results/{result_id}").json()
    pred = detail["predictions"][0]
    assert pred["edited_json"] == out["edited_json"]
    original = LocationResult(detections=[_detection(0.0, 0.0, 0.5, 0.5)])
    assert pred["parsed_json"] == original.model_dump_json()
    assert detail["location_score"]["f1"] == f1_before


def test_overlay_redraws_from_the_edit_then_reverts(client, engine, tmp_path):
    result_id = _seed_location(engine, tmp_path)

    body = {"edited_json": _edited(_detection(0.2, 0.2, 0.8, 0.8))}
    client.put(f"/api/results/{result_id}/pages/1/prediction", json=body)
    # The overlay route now serves an on-demand PNG rendered from the edit.
    overlay = client.get(f"/api/results/{result_id}/pages/1/overlay")
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"
    assert overlay.content.startswith(b"\x89PNG")

    # Revert clears the edit; the drill-down shows the model's output again.
    revert = client.delete(f"/api/results/{result_id}/pages/1/prediction")
    assert revert.status_code == 200
    assert revert.json()["edited_json"] is None
    detail = client.get(f"/api/results/{result_id}").json()
    assert detail["predictions"][0]["edited_json"] is None


def test_invalid_override_is_400_and_nothing_persisted(client, engine, tmp_path):
    result_id = _seed_location(engine, tmp_path)
    bad = '{"detections": [{"label": "walls", "bounding_box": {"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1}}]}'
    resp = client.put(
        f"/api/results/{result_id}/pages/1/prediction", json={"edited_json": bad}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]
    # Nothing was persisted — the drill-down still shows no edit.
    detail = client.get(f"/api/results/{result_id}").json()
    assert detail["predictions"][0]["edited_json"] is None


def test_counting_prediction_rejects_the_edit(client, engine):
    result_id = _seed_counting(engine)
    resp = client.put(
        f"/api/results/{result_id}/pages/1/prediction",
        json={"edited_json": _edited(_detection(0.1, 0.1, 0.2, 0.2))},
    )
    assert resp.status_code == 400


def test_unknown_prediction_is_404(client, engine, tmp_path):
    result_id = _seed_location(engine, tmp_path)
    resp = client.put(
        f"/api/results/{result_id}/pages/99/prediction",
        json={"edited_json": _edited(_detection(0.1, 0.1, 0.2, 0.2))},
    )
    assert resp.status_code == 404
