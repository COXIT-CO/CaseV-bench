"""JSON contract for the Leaderboard (spec §A.2, ticket 02). The ``/api`` twin serves the
same ranked board as the live Jinja ``/leaderboard`` route — same service methods, JSON
instead of HTML — so the React SPA can render the Landing page. A Drawing with no GT shows
its Results as unscored (``scored:false``, ``rank:null``), distinct from a zero score.
"""

import json
import time

import pytest
from conftest import LOCATION_BOXES_JSON, seed_location_drawing_id
from sqlmodel import Session, select

from core.models.prompt import Prompt
from core.models.run import Result

ACCURATE = "anthropic/claude-sonnet-4.5"
SLOPPY = "openai/gpt-5-mini"
# The accurate model returns the GT box exactly (P/R/F1 = 1.0); the sloppy one puts its
# cabinet somewhere else entirely, so nothing matches and every rate is 0.0.
ACCURATE_JSON = LOCATION_BOXES_JSON
SLOPPY_JSON = json.dumps(
    [
        {
            "label": "cabinet",
            "bounding_box": {"x_min": 0.7, "y_min": 0.7, "x_max": 0.9, "y_max": 0.9},
        }
    ]
)


@pytest.fixture
def import_gt(engine, seed_location_gt):
    """Import the shared answer key for a Drawing, in a Session of its own — this module
    drives everything over HTTP and holds none."""

    def _import(drawing_id: int) -> None:
        with Session(engine) as session:
            seed_location_gt(session, drawing_id)

    return _import


def _launch_and_wait(client, drawing_id, prompt_id, models=(ACCURATE, SLOPPY)):
    launched = client.post(
        "/api/runs",
        json={
            "prompt_id": prompt_id,
            "drawing_id": drawing_id,
            "models": list(models),
        },
    )
    run_id = launched.json()["id"]
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if client.get(f"/api/runs/{run_id}/status").json()["status"] == "done":
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_leaderboard_api_ranks_scored_rows(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service, import_gt
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, drawing_id, location_prompt.id)
    import_gt(drawing_id)

    response = client.get(f"/api/leaderboard?drawing_id={drawing_id}")
    assert response.status_code == 200
    body = response.json()

    # Filter surface: the ranking metrics and the drawings dropdown.
    assert body["sort"] == "f1"
    assert body["metrics"] == ["f1", "precision", "recall"]
    assert body["label_count"] == 4
    assert body["drawing_id"] == drawing_id
    assert {"id": drawing_id, "name": "sample", "page_count": 1} in body["drawings"]

    # Rows come back already ranked best-first; the accurate model (F1 1.0) is rank 1.
    rows = body["rows"]
    assert len(rows) == 2
    assert [r["model"] for r in rows] == [ACCURATE, SLOPPY]
    top = rows[0]
    assert top["rank"] == 1
    assert top["scored"] is True
    assert top["f1"] == 1.0
    assert top["precision"] == 1.0
    assert top["recall"] == 1.0
    assert top["prompt_family"] and top["prompt_version"] >= 1
    # Rows carry the Drawing they scored so the SPA can show it and build the GT CTA.
    assert top["drawing_id"] == drawing_id
    assert top["drawing_name"] == "sample"
    assert rows[1]["rank"] == 2


def test_leaderboard_api_sort_by_precision(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service, import_gt
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, drawing_id, location_prompt.id)
    import_gt(drawing_id)

    body = client.get(f"/api/leaderboard?drawing_id={drawing_id}&sort=precision").json()
    assert body["sort"] == "precision"


def test_leaderboard_api_unknown_sort_falls_back_to_the_default_metric(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, drawing_id, location_prompt.id)

    body = client.get(f"/api/leaderboard?drawing_id={drawing_id}&sort=bogus").json()
    # A stale/unknown metric never 500s; it falls back to the board default (spec §A.2).
    assert body["sort"] == "f1"


def test_leaderboard_api_unscored_rows_have_null_rank_and_metrics(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, drawing_id, location_prompt.id)

    # No GT imported → every Result is unscored, distinct from a zero score.
    rows = client.get(f"/api/leaderboard?drawing_id={drawing_id}").json()["rows"]
    assert len(rows) == 2
    assert all(r["scored"] is False for r in rows)
    assert all(r["rank"] is None for r in rows)
    assert all(r["f1"] is None for r in rows)


def test_leaderboard_api_filters_by_prompt_family(
    client, engine, stub_adapter, temp_overlay_run_service, import_gt
):
    """A ``prompt_family`` narrows the board to that lineage (ticket 04)."""
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    with Session(engine) as session:
        terse = Prompt(family="terse", version=1, text="find")
        verbose = Prompt(family="verbose", version=1, text="find well")
        session.add(terse)
        session.add(verbose)
        session.commit()
        terse_id, verbose_id = terse.id, verbose.id
    _launch_and_wait(client, drawing_id, terse_id, [ACCURATE])
    _launch_and_wait(client, drawing_id, verbose_id, [SLOPPY])
    import_gt(drawing_id)

    body = client.get(
        f"/api/leaderboard?drawing_id={drawing_id}&prompt_family=terse"
    ).json()
    assert body["prompt_family"] == "terse"
    assert {r["prompt_family"] for r in body["rows"]} == {"terse"}
    assert [r["model"] for r in body["rows"]] == [ACCURATE]


def test_leaderboard_api_filters_by_prompt_family_and_version(
    client, engine, stub_adapter, temp_overlay_run_service, import_gt
):
    """A ``prompt_family`` + ``prompt_version`` pins one exact version (ticket 04)."""
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    with Session(engine) as session:
        v1 = Prompt(family="custom", version=1, text="find")
        v2 = Prompt(family="custom", version=2, text="find more")
        session.add(v1)
        session.add(v2)
        session.commit()
        v1_id, v2_id = v1.id, v2.id
    _launch_and_wait(client, drawing_id, v1_id, [ACCURATE])
    _launch_and_wait(client, drawing_id, v2_id, [SLOPPY])
    import_gt(drawing_id)

    body = client.get(
        f"/api/leaderboard?drawing_id={drawing_id}"
        f"&prompt_family=custom&prompt_version=2"
    ).json()
    assert body["prompt_family"] == "custom"
    assert body["prompt_version"] == 2
    assert [(r["prompt_family"], r["prompt_version"]) for r in body["rows"]] == [
        ("custom", 2)
    ]
    assert [r["model"] for r in body["rows"]] == [SLOPPY]


def test_leaderboard_api_version_without_family_is_400(client):
    """A ``prompt_version`` without a ``prompt_family`` is ambiguous → 400 (ticket 04)."""
    response = client.get("/api/leaderboard?prompt_version=2")
    assert response.status_code == 400
    assert "prompt_family" in response.json()["detail"]


def test_leaderboard_api_empty_board(client):
    body = client.get("/api/leaderboard").json()
    assert body["rows"] == []


def test_leaderboard_api_ignores_a_stale_task_parameter(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service, import_gt
):
    """There is one board and no ``task`` parameter (ADR 0032). A stale client still
    sending ``?task=counting`` gets that board with its rates all the same — the parameter
    is ignored, not honoured and not an error."""
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, drawing_id, location_prompt.id)
    import_gt(drawing_id)

    body = client.get("/api/leaderboard?task=counting").json()

    assert body["sort"] == "f1"
    assert [row["f1"] for row in body["rows"]] == [1.0, 0.0]


def test_leaderboard_api_result_ids_link_to_detail(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, drawing_id, location_prompt.id)

    rows = client.get(f"/api/leaderboard?drawing_id={drawing_id}").json()["rows"]
    with Session(engine) as session:
        real_ids = {r.id for r in session.exec(select(Result)).all()}
    assert {r["result_id"] for r in rows} == real_ids
