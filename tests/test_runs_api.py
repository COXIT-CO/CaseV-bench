"""JSON contract for Runs (spec §A.4, ticket 04). These are the twins of the Jinja
``/runs`` launch page and its HTMX status poll: the launch-form option set, the create
endpoint that resolves the slug list server-side and returns the queued Run, the run
history, the detail (header + fixed-knobs snapshot + result rows), and the small polling
status endpoint that stops at a terminal state. The adapter is stubbed via the ``app``
fixture, so the background run finishes without hitting the network."""

import time
from pathlib import Path

from conftest import seed_page_images
from sqlmodel import Session, select

from core.models.drawing import Drawing, Page
from core.models.prompt import Prompt, Task

SONNET = "anthropic/claude-sonnet-4.5"
COUNT_JSON = (
    '{"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)


def _seed_drawing(engine, name: str = "sample") -> int:
    # The page points at a real native raster (under the engine's temp data dir) so the
    # background run's render-on-demand has an image to hand the stubbed Model.
    data_dir = Path(engine.url.database).parent
    with Session(engine) as session:
        drawing = Drawing(name=name)
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        (image,) = seed_page_images(data_dir / "drawings" / str(drawing.id), n_pages=1)
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=1,
                image_path=str(image),
                width_px=100,
                height_px=100,
            )
        )
        session.commit()
        return drawing.id


def _counting_prompt_id(engine) -> int:
    # The default counting family is seeded on app startup (lifespan).
    with Session(engine) as session:
        return (
            session.exec(select(Prompt).where(Prompt.task == Task.counting)).first().id
        )


def _launch(client, engine, drawing_id, **body) -> dict:
    payload = {"prompt_id": _counting_prompt_id(engine), "drawing_id": drawing_id}
    payload.update(body)
    resp = client.post("/api/runs", json=payload)
    return resp


def test_launch_options_lists_prompts_drawings_and_catalog(client, engine):
    _seed_drawing(engine)
    body = client.get("/api/runs/launch-options").json()

    # Both seeded Tasks' default families are offered so the prompt's Task drives the Run.
    tasks = {p["task"] for p in body["prompts"]}
    assert {"counting", "location"} <= tasks
    assert body["drawings"][0] == {"id": 1, "name": "sample", "page_count": 1}
    slugs = {c["slug"] for c in body["catalog"]}
    assert SONNET in slugs


def test_launch_options_empty_drawings_when_none_uploaded(client):
    body = client.get("/api/runs/launch-options").json()
    # No drawings yet — the SPA guides the user to create one rather than a dead form.
    assert body["drawings"] == []
    # Prompts stay seeded, so the emptiness is specifically the drawings.
    assert body["prompts"]


def test_create_run_returns_queued_run_and_detail_carries_knobs(
    client, engine, stub_adapter
):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    resp = _launch(client, engine, drawing_id, models=[SONNET], free_text="")
    assert resp.status_code == 201
    created = resp.json()
    assert created["status"] == "queued"
    assert created["task"] == "counting"
    assert created["total_units"] == 1

    detail = client.get(f"/api/runs/{created['id']}").json()
    assert detail["run"]["id"] == created["id"]
    assert detail["prompt"] == {"family": "default", "version": 1}
    assert detail["drawing"] == {"id": drawing_id, "name": "sample"}
    assert set(detail["knobs"]) == {
        "dpi",
        "downsample_px",
        "max_tokens",
        "temperature",
    }
    assert [r["model"] for r in detail["results"]] == [SONNET]


def test_create_run_snapshots_and_sends_advanced_knobs(client, engine, stub_adapter):
    # The Advanced section's max_tokens + explicit temperature are snapshotted on the Run
    # and reach the Model on the request (ticket 04).
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    resp = _launch(
        client, engine, drawing_id, models=[SONNET], max_tokens=8192, temperature=0.7
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]
    _poll_status(client, run_id)

    knobs = client.get(f"/api/runs/{run_id}").json()["knobs"]
    assert knobs["max_tokens"] == 8192
    assert knobs["temperature"] == 0.7

    call = stub_adapter.calls[-1]
    assert call["max_tokens"] == 8192
    assert call["temperature"] == 0.7


def test_create_run_provider_default_temperature_is_omitted(
    client, engine, stub_adapter
):
    # "Provider default" for temperature arrives as null, snapshots as None, and is omitted
    # from the request so a reasoning Model that rejects an explicit temperature still runs.
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    resp = _launch(client, engine, drawing_id, models=[SONNET], temperature=None)
    assert resp.status_code == 201
    run_id = resp.json()["id"]
    _poll_status(client, run_id)

    assert client.get(f"/api/runs/{run_id}").json()["knobs"]["temperature"] is None
    assert stub_adapter.calls[-1]["temperature"] is None


def test_create_run_defaults_knobs_when_advanced_untouched(
    client, engine, stub_adapter
):
    # The common one-click launch (no Advanced fields) keeps the pre-filled defaults.
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    run_id = _launch(client, engine, drawing_id, models=[SONNET]).json()["id"]
    knobs = client.get(f"/api/runs/{run_id}").json()["knobs"]
    assert knobs["dpi"] == 300
    assert knobs["downsample_px"] == 1568
    assert knobs["max_tokens"] == 4096
    assert knobs["temperature"] == 0.0


def test_create_run_snapshots_dpi_and_downsample(client, engine, stub_adapter):
    # The Advanced section's DPI + downsample are snapshotted on the Run (ticket 05); they make
    # the per-run render effective rather than reusing the fixed ingest downsample.
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    resp = _launch(
        client, engine, drawing_id, models=[SONNET], dpi=600, downsample_px=2000
    )
    assert resp.status_code == 201
    knobs = client.get(f"/api/runs/{resp.json()['id']}").json()["knobs"]
    assert knobs["dpi"] == 600
    assert knobs["downsample_px"] == 2000


def test_create_run_downsample_off_is_full_resolution(client, engine, stub_adapter):
    # downsample: null selects full resolution (no downsample), snapshotted as None.
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    resp = _launch(client, engine, drawing_id, models=[SONNET], downsample_px=None)
    assert resp.status_code == 201
    assert (
        client.get(f"/api/runs/{resp.json()['id']}").json()["knobs"]["downsample_px"]
        is None
    )


def test_create_run_rejects_nonpositive_max_tokens(client, engine):
    drawing_id = _seed_drawing(engine)
    resp = _launch(client, engine, drawing_id, models=[SONNET], max_tokens=0)
    assert resp.status_code == 422


def test_create_run_rejects_nonpositive_dpi(client, engine):
    drawing_id = _seed_drawing(engine)
    resp = _launch(client, engine, drawing_id, models=[SONNET], dpi=0)
    assert resp.status_code == 422


def test_leaderboard_does_not_split_rows_by_knob(client, engine, stub_adapter):
    # Two Runs of the same (prompt, model) differing only in a knob each keep their own
    # Result row under that (prompt, model) — the knob is not a ranking axis (ADR 0018).
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}
    _launch(client, engine, drawing_id, models=[SONNET], max_tokens=4096)
    _launch(client, engine, drawing_id, models=[SONNET], max_tokens=8192)

    rows = client.get("/api/leaderboard?task=counting").json()["rows"]
    sonnet_rows = [r for r in rows if r["model"] == SONNET]
    assert len(sonnet_rows) == 2
    assert {(r["prompt_family"], r["prompt_version"]) for r in sonnet_rows} == {
        ("default", 1)
    }


def test_create_run_resolves_free_text_slugs_server_side(client, engine):
    drawing_id = _seed_drawing(engine)
    # No curated selection: the free-text escape hatch is resolved as the source of truth.
    resp = _launch(
        client, engine, drawing_id, models=[], free_text="vendor/a, vendor/b"
    )
    assert resp.status_code == 201
    detail = client.get(f"/api/runs/{resp.json()['id']}").json()
    assert {r["model"] for r in detail["results"]} == {"vendor/a", "vendor/b"}


def test_create_run_bad_prompt_id_400(client, engine):
    drawing_id = _seed_drawing(engine)
    resp = client.post(
        "/api/runs",
        json={"prompt_id": 9999, "drawing_id": drawing_id, "models": [SONNET]},
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_create_run_empty_selection_400(client, engine):
    drawing_id = _seed_drawing(engine)
    resp = _launch(client, engine, drawing_id, models=[], free_text="")
    assert resp.status_code == 400


def test_run_history_lists_launched_runs_newest_first(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}
    run_id = _launch(client, engine, drawing_id, models=[SONNET]).json()["id"]

    runs = client.get("/api/runs").json()["runs"]
    row = next(r for r in runs if r["id"] == run_id)
    assert row["task"] == "counting"
    assert row["prompt_family"] == "default"
    assert row["prompt_version"] == 1
    assert row["drawing_name"] == "sample"
    assert row["total_units"] == 1
    assert row["created_at"]


def test_run_status_polls_to_done_with_results(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}
    run_id = _launch(client, engine, drawing_id, models=[SONNET]).json()["id"]

    status = _poll_status(client, run_id)
    assert status["status"] == "done"
    assert status["progress"] == status["total_units"] == 1
    assert [r["model"] for r in status["results"]] == [SONNET]


def test_delete_run_removes_it_and_returns_counts(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}
    run_id = _launch(client, engine, drawing_id, models=[SONNET]).json()["id"]
    _poll_status(client, run_id)  # let it finish so a Result exists

    resp = client.delete(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json() == {"runs": 1, "results": 1}

    # The Run is gone from both the detail endpoint and the history.
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert all(r["id"] != run_id for r in client.get("/api/runs").json()["runs"])


def test_delete_run_missing_404(client):
    assert client.delete("/api/runs/999").status_code == 404


def test_run_detail_missing_404(client):
    assert client.get("/api/runs/999").status_code == 404


def test_run_status_missing_404(client):
    assert client.get("/api/runs/999/status").status_code == 404


def _poll_status(client, run_id, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}/status").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")
