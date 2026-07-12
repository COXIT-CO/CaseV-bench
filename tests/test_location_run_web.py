"""Web-layer smoke check for location Runs (ticket 09): launching a Run against the
seeded location prompt executes the location path in the background, and once done the
per-page prediction overlay is served as a PNG. The adapter is stubbed via the ``app``
fixture and the overlay root is pointed at a temp dir so no repo data is written."""

import json
import time

from PIL import Image
from sqlmodel import Session, select

from adapters.openrouter import get_openrouter_adapter
from models.drawing import Drawing, Page
from models.prompt import Prompt, Task
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
        "/runs",
        data={"prompt_id": prompt_id, "drawing_id": drawing_id, "models": [SONNET]},
        follow_redirects=False,
    )
    assert launched.status_code == 303
    location = launched.headers["location"]

    status = _poll_status(client, f"{location}/status")
    assert "done" in status.text
    # The overlay image is wired into the results fragment...
    assert "/overlay" in status.text

    # ...and the overlay endpoint serves the rendered PNG.
    run_id = int(location.rsplit("/", 1)[1])
    result_id = _first_result_id(engine, run_id)
    overlay = client.get(f"/results/{result_id}/pages/1/overlay")
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"


def test_missing_overlay_returns_404(client, engine):
    # A (result, page) with no overlay (e.g. a non-existent one) 404s rather than 500s.
    missing = client.get("/results/999/pages/1/overlay")
    assert missing.status_code == 404


def _first_result_id(engine, run_id) -> int:
    from models.run import Result

    with Session(engine) as session:
        return session.exec(select(Result).where(Result.run_id == run_id)).first().id


def _poll_status(client, url, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(url)
        assert status.status_code == 200
        if "done" in status.text or "failed" in status.text:
            return status
        time.sleep(0.02)
    raise AssertionError(f"run did not finish within {timeout}s")
