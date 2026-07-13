"""End-to-end check that a location Result scores on the location path (ticket 11),
through the live Run flow and the JSON drill-down. The Jinja Leaderboard board (ticket 02)
and Result detail (ticket 03) were both retired — the location board lives in the React
SPA against ``GET /api/leaderboard?task=location`` (see
``tests/test_location_leaderboard_api.py``) and the drill-down against
``GET /api/results/{id}`` (see ``tests/test_result_detail_api.py``). Here we assert the
run-then-read path yields an IoU@0.5 P/R/F1 score, never a counting score."""

import json
import time

from PIL import Image
from sqlmodel import Session, select

from models.drawing import Drawing, Page
from models.prompt import Prompt, Task
from services.location_ground_truth import LocationGroundTruthService
from services.run import RunService
from web.app import get_run_service

SONNET = "anthropic/claude-sonnet-4.5"
BOXES_JSON = json.dumps(
    [
        {
            "label": "cabinets",
            "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4},
        }
    ]
)


def _seed_drawing(engine, tmp_path) -> int:
    with Session(engine) as session:
        drawing = Drawing(name="sample")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        image_path = tmp_path / "page_1.png"
        Image.new("RGB", (100, 100), "white").save(image_path)
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
        return drawing.id


def _location_prompt_id(engine) -> int:
    with Session(engine) as session:
        return (
            session.exec(select(Prompt).where(Prompt.task == Task.location)).first().id
        )


def _import_gt(engine, drawing_id) -> None:
    with Session(engine) as session:
        LocationGroundTruthService(session).import_coco(
            drawing_id,
            {
                "images": [
                    {"id": 1, "file_name": "page_0001.png", "width": 100, "height": 100}
                ],
                "categories": [{"id": 1, "name": "cabinets"}],
                "annotations": [
                    {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 30, 30]}
                ],
            },
        )


def _launch_and_wait(app, client, engine, stub_adapter, tmp_path, drawing_id):
    app.dependency_overrides[get_run_service] = lambda: RunService(
        Session(engine), stub_adapter, overlay_root=tmp_path / "overlays"
    )
    stub_adapter.responses = {SONNET: BOXES_JSON}
    launched = client.post(
        "/runs",
        data={
            "prompt_id": _location_prompt_id(engine),
            "drawing_id": drawing_id,
            "models": [SONNET],
        },
        follow_redirects=False,
    )
    location = launched.headers["location"]
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if "done" in client.get(f"{location}/status").text:
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_location_result_detail_shows_iou_score(
    app, client, engine, stub_adapter, tmp_path
):
    drawing_id = _seed_drawing(engine, tmp_path)
    _launch_and_wait(app, client, engine, stub_adapter, tmp_path, drawing_id)
    _import_gt(engine, drawing_id)

    with Session(engine) as session:
        from models.run import Result

        result_id = session.exec(select(Result)).first().id
    detail = client.get(f"/api/results/{result_id}")
    assert detail.status_code == 200
    body = detail.json()
    # A location Result uses IoU@0.5 P/R/F1 and never gets a counting score.
    assert body["task"] == "location"
    assert body["location_score"] is not None
    assert body["counting_score"] is None
