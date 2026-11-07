"""JSON contract for Runs (spec §A.4, ticket 04). These are the twins of the Jinja
``/runs`` launch page and its HTMX status poll: the launch-form option set, the create
endpoint that resolves the slug list server-side and returns the queued Run, the run
history, the detail (header + fixed-knobs snapshot + result rows), and the small polling
status endpoint that stops at a terminal state. The adapter is stubbed via the ``app``
fixture, so the background run finishes without hitting the network."""

import time

from conftest import LOCATION_BOXES_JSON, seed_location_drawing_id

from core.adapters.openrouter import DEFAULT_MAX_TOKENS, DEFAULT_REASONING_EFFORT
from core.services.model_catalog import DEFAULT_MODEL_CATALOG

SONNET = "anthropic/claude-sonnet-4.5"
# Two roster Models with different effort ceilings: Qwen takes the whole band, the Gemini line
# stops at ``high``.
QWEN = "qwen/qwen3.8-max"
GEMINI_FLASH = "google/gemini-3.7-flash"


def _launch(client, prompt_id, drawing_id, **body) -> dict:
    payload = {"prompt_id": prompt_id, "drawing_id": drawing_id}
    payload.update(body)
    resp = client.post("/api/runs", json=payload)
    return resp


def test_launch_options_lists_prompts_drawings_and_catalog(client, engine):
    seed_location_drawing_id(engine)
    body = client.get("/api/runs/launch-options").json()

    # The seeded default family is offered.
    assert any(p["family"] == "default" for p in body["prompts"])
    assert body["drawings"][0] == {"id": 1, "name": "sample", "page_count": 1}
    slugs = {c["slug"] for c in body["catalog"]}
    assert slugs == {slug for slug, _ in DEFAULT_MODEL_CATALOG}


def test_launch_options_empty_drawings_when_none_uploaded(client):
    body = client.get("/api/runs/launch-options").json()
    # No drawings yet — the SPA guides the user to create one rather than a dead form.
    assert body["drawings"] == []
    # Prompts stay seeded, so the emptiness is specifically the drawings.
    assert body["prompts"]


def test_create_run_returns_queued_run_and_detail_carries_knobs(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    resp = _launch(
        client, location_prompt.id, drawing_id, models=[SONNET], free_text=""
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["status"] == "queued"
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
        "reasoning_effort",
    }
    assert [r["model"] for r in detail["results"]] == [SONNET]


def test_create_run_snapshots_and_sends_advanced_knobs(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    # The Advanced section's max_tokens + explicit temperature are snapshotted on the Run
    # and reach the Model on the request (ticket 04).
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    resp = _launch(
        client,
        location_prompt.id,
        drawing_id,
        models=[SONNET],
        max_tokens=8192,
        temperature=0.7,
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
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    # "Provider default" for temperature arrives as null, snapshots as None, and is omitted
    # from the request so a reasoning Model that rejects an explicit temperature still runs.
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    resp = _launch(
        client, location_prompt.id, drawing_id, models=[SONNET], temperature=None
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]
    _poll_status(client, run_id)

    assert client.get(f"/api/runs/{run_id}").json()["knobs"]["temperature"] is None
    assert stub_adapter.calls[-1]["temperature"] is None


def test_create_run_snapshots_and_sends_reasoning_effort(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    # The chosen effort is snapshotted on the Run and is what every Model was asked for.
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    run_id = _launch(
        client,
        location_prompt.id,
        drawing_id,
        models=[SONNET],
        reasoning_effort="high",
    ).json()["id"]
    _poll_status(client, run_id)

    knobs = client.get(f"/api/runs/{run_id}").json()["knobs"]
    assert knobs["reasoning_effort"] == "high"
    assert stub_adapter.calls[-1]["reasoning_effort"] == "high"


def test_create_run_allows_xhigh_when_every_selected_model_accepts_it(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {QWEN: LOCATION_BOXES_JSON}

    resp = _launch(
        client,
        location_prompt.id,
        drawing_id,
        models=[QWEN],
        reasoning_effort="xhigh",
    )

    assert resp.status_code == 201
    run_id = resp.json()["id"]
    _poll_status(client, run_id)
    assert client.get(f"/api/runs/{run_id}").json()["knobs"]["reasoning_effort"] == (
        "xhigh"
    )
    assert stub_adapter.calls[-1]["reasoning_effort"] == "xhigh"


def test_create_run_rejects_an_effort_one_selected_model_cannot_answer(
    client, engine, location_prompt, temp_overlay_run_service
):
    # The Gemini line stops at ``high``. Queuing this would leave the Run with Results at two
    # different settings — the comparability the single knob exists to protect — so it is
    # refused at the edge rather than surfacing as one model's error mid-fan-out.
    drawing_id = seed_location_drawing_id(engine)

    resp = _launch(
        client,
        location_prompt.id,
        drawing_id,
        models=[QWEN, GEMINI_FLASH],
        reasoning_effort="xhigh",
    )

    assert resp.status_code == 422
    assert "xhigh" in resp.json()["detail"]


def test_create_run_rejects_an_effort_outside_the_vocabulary(
    client, engine, location_prompt, temp_overlay_run_service
):
    # ``max`` exists on one Model only and is not in the band at all.
    drawing_id = seed_location_drawing_id(engine)

    resp = _launch(
        client,
        location_prompt.id,
        drawing_id,
        models=[QWEN],
        reasoning_effort="max",
    )

    assert resp.status_code == 422


def test_launch_options_report_each_model_effort_ceiling(client, engine):
    # What the form needs to offer the selection's shared band instead of letting a Run be
    # launched that one Model would reject.
    seed_location_drawing_id(engine)

    catalog = client.get("/api/runs/launch-options").json()["catalog"]
    ceilings = {c["slug"]: c["max_reasoning_effort"] for c in catalog}

    assert ceilings[GEMINI_FLASH] == "high"
    assert ceilings[QWEN] == "xhigh"


def test_create_run_defaults_knobs_when_advanced_untouched(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    # The common one-click launch (no Advanced fields) keeps the pre-filled defaults.
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    run_id = _launch(client, location_prompt.id, drawing_id, models=[SONNET]).json()[
        "id"
    ]
    knobs = client.get(f"/api/runs/{run_id}").json()["knobs"]
    assert knobs["dpi"] == 300
    assert knobs["downsample_px"] == 1568
    assert knobs["max_tokens"] == DEFAULT_MAX_TOKENS
    # The pre-filled temperature is the provider default: an explicit one is what a reasoning
    # Model rejects, and one catalog Model does not accept the parameter at all.
    assert knobs["temperature"] is None
    assert knobs["reasoning_effort"] == DEFAULT_REASONING_EFFORT


def test_create_run_snapshots_dpi_and_downsample(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    # The Advanced section's DPI + downsample are snapshotted on the Run (ticket 05); they make
    # the per-run render effective rather than reusing the fixed ingest downsample.
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    resp = _launch(
        client,
        location_prompt.id,
        drawing_id,
        models=[SONNET],
        dpi=600,
        downsample_px=2000,
    )
    assert resp.status_code == 201
    knobs = client.get(f"/api/runs/{resp.json()['id']}").json()["knobs"]
    assert knobs["dpi"] == 600
    assert knobs["downsample_px"] == 2000


def test_create_run_downsample_off_is_full_resolution(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    # downsample: null selects full resolution (no downsample), snapshotted as None.
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    resp = _launch(
        client, location_prompt.id, drawing_id, models=[SONNET], downsample_px=None
    )
    assert resp.status_code == 201
    assert (
        client.get(f"/api/runs/{resp.json()['id']}").json()["knobs"]["downsample_px"]
        is None
    )


def test_create_run_rejects_nonpositive_max_tokens(client, engine, location_prompt):
    drawing_id = seed_location_drawing_id(engine)
    resp = _launch(
        client, location_prompt.id, drawing_id, models=[SONNET], max_tokens=0
    )
    assert resp.status_code == 422


def test_create_run_rejects_nonpositive_dpi(client, engine, location_prompt):
    drawing_id = seed_location_drawing_id(engine)
    resp = _launch(client, location_prompt.id, drawing_id, models=[SONNET], dpi=0)
    assert resp.status_code == 422


def test_leaderboard_does_not_split_rows_by_knob(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    # Two Runs of the same (prompt, model) differing only in a knob each keep their own
    # Result row under that (prompt, model) — the knob is not a ranking axis (ADR 0018).
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}
    _launch(client, location_prompt.id, drawing_id, models=[SONNET], max_tokens=4096)
    _launch(client, location_prompt.id, drawing_id, models=[SONNET], max_tokens=8192)

    rows = client.get("/api/leaderboard").json()["rows"]
    sonnet_rows = [r for r in rows if r["model"] == SONNET]
    assert len(sonnet_rows) == 2
    assert {(r["prompt_family"], r["prompt_version"]) for r in sonnet_rows} == {
        ("default", 1)
    }


def test_create_run_resolves_free_text_slugs_server_side(
    client, engine, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    # No curated selection: the free-text escape hatch is resolved as the source of truth.
    resp = _launch(
        client,
        location_prompt.id,
        drawing_id,
        models=[],
        free_text="vendor/a, vendor/b",
    )
    assert resp.status_code == 201
    detail = client.get(f"/api/runs/{resp.json()['id']}").json()
    assert {r["model"] for r in detail["results"]} == {"vendor/a", "vendor/b"}


def test_create_run_bad_prompt_id_400(client, engine):
    drawing_id = seed_location_drawing_id(engine)
    resp = client.post(
        "/api/runs",
        json={"prompt_id": 9999, "drawing_id": drawing_id, "models": [SONNET]},
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_create_run_empty_selection_400(client, engine, location_prompt):
    drawing_id = seed_location_drawing_id(engine)
    resp = _launch(client, location_prompt.id, drawing_id, models=[], free_text="")
    assert resp.status_code == 400


def test_run_history_lists_launched_runs_newest_first(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}
    run_id = _launch(client, location_prompt.id, drawing_id, models=[SONNET]).json()[
        "id"
    ]

    runs = client.get("/api/runs").json()["runs"]
    row = next(r for r in runs if r["id"] == run_id)
    assert row["prompt_family"] == "default"
    assert row["prompt_version"] == 1
    assert row["drawing_name"] == "sample"
    assert row["total_units"] == 1
    assert row["created_at"]


def test_run_status_polls_to_done_with_results(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}
    run_id = _launch(client, location_prompt.id, drawing_id, models=[SONNET]).json()[
        "id"
    ]

    status = _poll_status(client, run_id)
    assert status["status"] == "done"
    assert status["progress"] == status["total_units"] == 1
    assert [r["model"] for r in status["results"]] == [SONNET]


def test_delete_run_removes_it_and_returns_counts(
    client, engine, stub_adapter, location_prompt, temp_overlay_run_service
):
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}
    run_id = _launch(client, location_prompt.id, drawing_id, models=[SONNET]).json()[
        "id"
    ]
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
