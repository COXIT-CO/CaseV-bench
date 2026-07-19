"""JSON contract for the location Leaderboard (spec §A.2, ticket 02). Under
``?task=location`` the ``/api`` twin serves the P/R/F1-ranked board — the location metric
set and per-row rates instead of the counting pair — with unscored Results pinned last.
"""

import json
import time

from conftest import seed_page_images
from sqlmodel import Session, select

from api.deps import get_run_service
from core.adapters.openrouter import get_openrouter_adapter
from core.models.drawing import Drawing, Page
from core.models.prompt import Prompt, Task
from core.services.location_ground_truth import LocationGroundTruthService
from core.services.run import RunService

SONNET = "anthropic/claude-sonnet-4.5"
BOXES_JSON = json.dumps(
    [
        {
            "label": "cabinet",
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
        (image_path,) = seed_page_images(tmp_path / str(drawing.id), n_pages=1)
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=1,
                image_path=str(image_path),
                width_px=100,
                height_px=100,
                native_width_pt=100.0,
                native_height_pt=100.0,
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
        LocationGroundTruthService(session).import_objects(
            drawing_id,
            {
                "objects": [
                    {
                        "id": "a",
                        "category": "cabinet",
                        "page": 1,
                        "bbox": {"x": 10, "y": 10, "width": 30, "height": 30},
                    }
                ],
            },
        )


def _launch_and_wait(app, client, engine, stub_adapter, tmp_path, drawing_id):
    app.dependency_overrides[get_run_service] = lambda: RunService(
        Session(engine), stub_adapter, overlay_root=tmp_path / "overlays"
    )
    stub_adapter.responses = {SONNET: BOXES_JSON}
    launched = client.post(
        "/api/runs",
        json={
            "prompt_id": _location_prompt_id(engine),
            "drawing_id": drawing_id,
            "models": [SONNET],
        },
    )
    run_id = launched.json()["id"]
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if client.get(f"/api/runs/{run_id}/status").json()["status"] == "done":
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_location_leaderboard_api_ranks_by_prf1(
    app, client, engine, stub_adapter, tmp_path
):
    drawing_id = _seed_drawing(engine, tmp_path)
    _launch_and_wait(app, client, engine, stub_adapter, tmp_path, drawing_id)
    _import_gt(engine, drawing_id)

    body = client.get(f"/api/leaderboard?task=location&drawing_id={drawing_id}").json()

    assert body["task"] == "location"
    assert body["sort"] == "f1"
    assert body["metrics"] == ["f1", "precision", "recall"]

    rows = body["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["rank"] == 1
    assert row["scored"] is True
    assert row["model"] == SONNET
    # A single perfectly-matched box → P/R/F1 all 1.0.
    assert row["f1"] == 1.0
    assert row["precision"] == 1.0
    assert row["recall"] == 1.0
    # Location rows carry no counting metrics.
    assert row["total_absolute_error"] is None


def test_location_leaderboard_api_unscored_without_gt(
    app, client, engine, stub_adapter, tmp_path
):
    drawing_id = _seed_drawing(engine, tmp_path)
    _launch_and_wait(app, client, engine, stub_adapter, tmp_path, drawing_id)

    rows = client.get(f"/api/leaderboard?task=location&drawing_id={drawing_id}").json()[
        "rows"
    ]
    assert len(rows) == 1
    assert rows[0]["scored"] is False
    assert rows[0]["rank"] is None
    assert rows[0]["f1"] is None
