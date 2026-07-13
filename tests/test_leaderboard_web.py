"""Web-layer check for the still-live HTMX Result drill-down (ticket 08). The Jinja
Leaderboard board was retired in ticket 02 — it now lives in the React SPA against
``GET /api/leaderboard`` (see ``tests/test_leaderboard_api.py``). The counting
``/results/{id}`` detail page stays on HTMX until the result-detail slice (ticket 03), so
its per-page Predictions + raw JSON + score summary are still covered here."""

import time

from sqlmodel import Session, select

from models.drawing import Drawing, Page
from models.prompt import Prompt, Task

ACCURATE = "anthropic/claude-sonnet-4.5"
SLOPPY = "openai/gpt-5-mini"
ACCURATE_JSON = (
    '{"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)
SLOPPY_JSON = (
    '{"cabinets": 9, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)
GT = {"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}


def _seed_drawing(engine) -> int:
    with Session(engine) as session:
        drawing = Drawing(name="sample")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=1,
                image_path="/tmp/page_1.png",
                width_px=100,
                height_px=100,
            )
        )
        session.commit()
        return drawing.id


def _counting_prompt_id(engine) -> int:
    with Session(engine) as session:
        return (
            session.exec(select(Prompt).where(Prompt.task == Task.counting)).first().id
        )


def _launch_and_wait(client, engine, drawing_id, prompt_id):
    launched = client.post(
        "/runs",
        data={
            "prompt_id": prompt_id,
            "drawing_id": drawing_id,
            "models": [ACCURATE, SLOPPY],
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


def test_counting_result_detail_shows_score_and_raw_output(
    client, engine, stub_adapter
):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))
    client.post(f"/drawings/{drawing_id}/counting-ground-truth", data=GT)

    with Session(engine) as session:
        from models.run import Result

        result_id = (
            session.exec(select(Result).where(Result.model == ACCURATE)).first().id
        )
    detail = client.get(f"/results/{result_id}")
    assert detail.status_code == 200
    assert "cabinets" in detail.text  # per-page parsed counts
    assert "Total absolute error" in detail.text  # counting score summary
    assert "Raw model output" in detail.text  # raw JSON section present
    # The retired board's back-link now points Home, not at /leaderboard.
    assert "/leaderboard" not in detail.text
