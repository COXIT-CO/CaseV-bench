"""Web-layer smoke check for location Runs (ticket 09) over the JSON API (spec §A.4):
launching a Run against the seeded location prompt executes the location path in the
background, and once done the per-page prediction overlay is served as a PNG under
``/api``. The adapter is stubbed via the ``app`` fixture and the overlay root is pointed
at a temp dir so no repo data is written."""

import json
import time

from conftest import seed_page_images
from sqlmodel import Session, select

from api.deps import get_run_service
from core.models.drawing import Drawing, Page
from core.models.prompt import Prompt, Task
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
            )
        )
        session.commit()
        return drawing.id


def _location_prompt_id(engine) -> int:
    with Session(engine) as session:
        return (
            session.exec(select(Prompt).where(Prompt.task == Task.location)).first().id
        )


def test_location_run_launches_and_serves_overlay(
    app, client, engine, stub_adapter, tmp_path
):
    # Point the run service's overlay root at a temp dir so the background run doesn't
    # write PNGs into the repo's data/ during the test.
    overlay_root = tmp_path / "overlays"
    app.dependency_overrides[get_run_service] = lambda: RunService(
        Session(engine), stub_adapter, overlay_root=overlay_root
    )
    stub_adapter.responses = {SONNET: BOXES_JSON}

    drawing_id = _seed_drawing(engine, tmp_path)
    prompt_id = _location_prompt_id(engine)

    launched = client.post(
        "/api/runs",
        json={"prompt_id": prompt_id, "drawing_id": drawing_id, "models": [SONNET]},
    )
    assert launched.status_code == 201
    run_id = launched.json()["id"]

    status = _poll_status(client, run_id)
    assert status["status"] == "done"

    # The overlay endpoint serves the rendered PNG under /api for the SPA.
    result_id = status["results"][0]["id"]
    overlay = client.get(f"/api/results/{result_id}/pages/1/overlay")
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"


def test_missing_overlay_returns_404(client, engine):
    # A (result, page) with no overlay (e.g. a non-existent one) 404s rather than 500s.
    missing = client.get("/api/results/999/pages/1/overlay")
    assert missing.status_code == 404


def _poll_status(client, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/runs/{run_id}/status")
        assert status.status_code == 200
        body = status.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")
