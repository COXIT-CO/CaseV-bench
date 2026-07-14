"""JSON contract for the counting Leaderboard (spec §A.2, ticket 02). The ``/api``
twin serves the same ranked board as the live Jinja ``/leaderboard`` route — same service
methods, JSON instead of HTML — so the React SPA can render the Landing page. A Drawing
with no GT shows its Results as unscored (``scored:false``, ``rank:null``), distinct from a
zero score."""

import time

from sqlmodel import Session, select

from core.models.drawing import Drawing, Page
from core.models.prompt import Prompt, Task
from core.models.run import Result

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
        "/api/runs",
        json={
            "prompt_id": prompt_id,
            "drawing_id": drawing_id,
            "models": [ACCURATE, SLOPPY],
        },
    )
    run_id = launched.json()["id"]
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if client.get(f"/api/runs/{run_id}/status").json()["status"] == "done":
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_leaderboard_api_ranks_scored_rows(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))
    client.put(f"/api/drawings/{drawing_id}/counting-ground-truth", json=GT)

    response = client.get(f"/api/leaderboard?drawing_id={drawing_id}")
    assert response.status_code == 200
    body = response.json()

    # Filter surface: default counting task, its ranking metrics, the drawings dropdown.
    assert body["task"] == "counting"
    assert body["sort"] == "total_absolute_error"
    assert body["metrics"] == ["total_absolute_error", "exact_match_count"]
    assert body["label_count"] == 4
    assert body["drawing_id"] == drawing_id
    assert {"id": drawing_id, "name": "sample", "page_count": 1} in body["drawings"]

    # Rows come back already ranked best-first; the accurate model (0 error) is rank 1.
    rows = body["rows"]
    assert len(rows) == 2
    assert [r["model"] for r in rows] == [ACCURATE, SLOPPY]
    top = rows[0]
    assert top["rank"] == 1
    assert top["scored"] is True
    assert top["total_absolute_error"] == 0
    assert top["exact_match_count"] == 4
    assert top["prompt_family"] and top["prompt_version"] >= 1
    # Rows carry the Drawing they scored so the SPA can show it and build the GT CTA.
    assert top["drawing_id"] == drawing_id
    assert top["drawing_name"] == "sample"
    assert rows[1]["rank"] == 2


def test_leaderboard_api_sort_by_exact_matches(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))
    client.put(f"/api/drawings/{drawing_id}/counting-ground-truth", json=GT)

    body = client.get(
        f"/api/leaderboard?drawing_id={drawing_id}&sort=exact_match_count"
    ).json()
    assert body["sort"] == "exact_match_count"


def test_leaderboard_api_unknown_sort_falls_back_to_task_default(
    client, engine, stub_adapter
):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))

    body = client.get(f"/api/leaderboard?drawing_id={drawing_id}&sort=bogus").json()
    # A stale/unknown metric never 500s; it falls back to the task default (spec §A.2).
    assert body["sort"] == "total_absolute_error"


def test_leaderboard_api_unscored_rows_have_null_rank_and_metrics(
    client, engine, stub_adapter
):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))

    # No GT entered → every Result is unscored, distinct from a zero score.
    rows = client.get(f"/api/leaderboard?drawing_id={drawing_id}").json()["rows"]
    assert len(rows) == 2
    assert all(r["scored"] is False for r in rows)
    assert all(r["rank"] is None for r in rows)
    assert all(r["total_absolute_error"] is None for r in rows)


def test_leaderboard_api_empty_board(client):
    body = client.get("/api/leaderboard").json()
    assert body["rows"] == []
    assert body["task"] == "counting"


def test_leaderboard_api_result_ids_link_to_detail(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))

    rows = client.get(f"/api/leaderboard?drawing_id={drawing_id}").json()["rows"]
    with Session(engine) as session:
        real_ids = {r.id for r in session.exec(select(Result)).all()}
    assert {r["result_id"] for r in rows} == real_ids
