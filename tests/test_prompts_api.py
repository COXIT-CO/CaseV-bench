"""JSON contract for Prompts (spec §A.5, ticket 05). These are the twins of the Jinja
``/prompts`` list, its authoring form, the version-history page, and its "edit" form:
families grouped by Task (Task-scoping, ADR 0009), creating a new family's v1, browsing a
family's immutable version history newest-first with each version's text (so compare needs
no extra round-trip), and appending the next immutable version. The two shipped prompts are
seeded on app startup, so both Tasks start with a ``default`` family.

Deleting a prompt at either granularity (ticket 09, ADR-0016) is covered at the bottom: the
DELETE routes cascade the pinning Runs and return the collateral counts, and the history
endpoint carries those counts up front so the confirm dialog can state the blast radius.
"""

import pytest
from sqlmodel import Session, select

from api.deps import get_prompt_service
from core.models.drawing import Drawing
from core.models.prompt import Prompt, Task
from core.models.run import Result, Run
from core.services.prompt import PromptService

SONNET = "anthropic/claude-sonnet-4.5"


def _pin_run(engine, prompt_id: int) -> int:
    """Attach a done Run (with one Result) pinning ``prompt_id`` so a delete has collateral.
    A throwaway Drawing gives the Run a valid ``drawing_id`` without any on-disk artifacts.
    """
    with Session(engine) as session:
        drawing = Drawing(name=f"d-for-{prompt_id}")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        run = Run(
            task=Task.location,
            prompt_id=prompt_id,
            drawing_id=drawing.id,
            dpi=200,
            downsample_px=1600,
            max_tokens=4096,
            prefill=True,
            temperature=0.0,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(Result(run_id=run.id, model=SONNET))
        session.commit()
        return run.id


def _author_family(engine, family: str, n_versions: int) -> list[int]:
    """Author ``family`` v1..vN and return the prompt-row ids, oldest-first."""
    with Session(engine) as session:
        service = PromptService(session)
        service.create(Task.location, family, "v1")
        for _ in range(2, n_versions + 1):
            service.edit(Task.location, family, "next")
        return [p.id for p in service.history(Task.location, family)[::-1]]


@pytest.fixture
def delete_capable_prompts(app, engine, tmp_path):
    """Override the prompt routes' service so its delete cascade cleans overlay files under a
    temp root rather than the real data root (mirrors the Drawing delete's override)."""

    def _svc():
        with Session(engine) as session:
            yield PromptService(session, overlay_root=tmp_path / "overlays")

    app.dependency_overrides[get_prompt_service] = _svc
    return _svc


def test_list_groups_seeded_families_by_task(client):
    body = client.get("/api/prompts").json()

    assert body["tasks"] == ["counting", "location"]
    groups = {g["task"]: g["families"] for g in body["groups"]}
    assert set(groups) == {"counting", "location"}
    # Each Task's seeded ``default`` family reports its latest version and version count.
    counting_default = next(f for f in groups["counting"] if f["name"] == "default")
    assert counting_default == {"name": "default", "latest_version": 1, "count": 1}
    assert any(f["name"] == "default" for f in groups["location"])


def test_create_makes_v1_and_lists_it_under_its_task(client):
    resp = client.post(
        "/api/prompts",
        json={"task": "counting", "family": "kitchen", "text": "count cabinets"},
    )
    assert resp.status_code == 201
    assert resp.json() == {"task": "counting", "family": "kitchen", "version": 1}

    groups = {
        g["task"]: g["families"] for g in client.get("/api/prompts").json()["groups"]
    }
    kitchen = next(f for f in groups["counting"] if f["name"] == "kitchen")
    assert kitchen == {"name": "kitchen", "latest_version": 1, "count": 1}
    # Task-scoped: a counting family is never offered under location.
    assert not any(f["name"] == "kitchen" for f in groups["location"])


def test_create_duplicate_family_is_400_with_service_message(client):
    client.post(
        "/api/prompts",
        json={"task": "counting", "family": "dupe", "text": "a"},
    )
    again = client.post(
        "/api/prompts",
        json={"task": "counting", "family": "dupe", "text": "b"},
    )
    assert again.status_code == 400
    assert "already exists" in again.json()["detail"]


def test_history_is_newest_first_with_each_versions_text(client):
    client.post(
        "/api/prompts",
        json={"task": "location", "family": "doors", "text": "locate v1"},
    )
    client.post(
        "/api/prompts/location/doors/versions",
        json={"text": "locate v2"},
    )

    body = client.get("/api/prompts/location/doors").json()
    assert body["task"] == "location"
    assert body["family"] == "doors"
    # Newest-first, and each version carries its text so compare needs no follow-up fetch.
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert [v["text"] for v in body["versions"]] == ["locate v2", "locate v1"]
    assert all(v["created_at"] for v in body["versions"])


def test_append_version_never_mutates_prior_versions(client):
    client.post(
        "/api/prompts",
        json={"task": "counting", "family": "cab", "text": "v1 text"},
    )
    resp = client.post(
        "/api/prompts/counting/cab/versions",
        json={"text": "v2 text"},
    )
    assert resp.status_code == 201
    assert resp.json() == {"task": "counting", "family": "cab", "version": 2}

    versions = {
        v["version"]: v["text"]
        for v in client.get("/api/prompts/counting/cab").json()["versions"]
    }
    # The v1 row is untouched by the edit (immutable append, ADR 0009).
    assert versions == {1: "v1 text", 2: "v2 text"}


def test_history_unknown_family_is_404(client):
    assert client.get("/api/prompts/counting/ghost").status_code == 404


def test_append_to_unknown_family_is_404(client):
    resp = client.post(
        "/api/prompts/counting/ghost/versions",
        json={"text": "x"},
    )
    assert resp.status_code == 404


def test_history_reports_delete_collateral_counts(client, engine):
    v1_id, v2_id = _author_family(engine, "cabinets", 2)
    _pin_run(engine, v1_id)  # one Run pins v1
    _pin_run(engine, v2_id)  # two Runs pin v2
    _pin_run(engine, v2_id)

    body = client.get("/api/prompts/location/cabinets").json()

    per_version = {v["version"]: v for v in body["versions"]}
    assert (per_version[1]["run_count"], per_version[1]["result_count"]) == (1, 1)
    assert (per_version[2]["run_count"], per_version[2]["result_count"]) == (2, 2)
    # The family total is the sum across versions, for the "delete family" confirm.
    assert body["run_count"] == 3
    assert body["result_count"] == 3


def test_delete_version_cascades_pinning_runs_and_returns_counts(
    client, engine, delete_capable_prompts
):
    v1_id, v2_id = _author_family(engine, "doors", 2)
    run_v1 = _pin_run(engine, v1_id)
    run_v2 = _pin_run(engine, v2_id)

    resp = client.delete("/api/prompts/location/doors/versions/1")
    assert resp.status_code == 200
    assert resp.json() == {"runs": 1, "results": 1}

    # v1 is gone from the history (now just v2) and its Run with it; v2's Run survives.
    versions = [
        v["version"]
        for v in client.get("/api/prompts/location/doors").json()["versions"]
    ]
    assert versions == [2]
    with Session(engine) as session:
        assert session.get(Run, run_v1) is None
        assert session.get(Run, run_v2) is not None


def test_delete_family_cascades_every_version_and_returns_counts(
    client, engine, delete_capable_prompts
):
    v1_id, v2_id = _author_family(engine, "windows", 2)
    _pin_run(engine, v1_id)
    _pin_run(engine, v2_id)

    resp = client.delete("/api/prompts/location/windows")
    assert resp.status_code == 200
    assert resp.json() == {"runs": 2, "results": 2}

    # The family no longer resolves, and no Run is left behind.
    assert client.get("/api/prompts/location/windows").status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Run)).all() == []
        assert (
            session.exec(select(Prompt).where(Prompt.family == "windows")).all() == []
        )


def test_delete_unknown_version_and_family_are_404(client, delete_capable_prompts):
    _author_family_via_api = client.post(
        "/api/prompts",
        json={"task": "location", "family": "present", "text": "v1"},
    )
    assert _author_family_via_api.status_code == 201
    assert client.delete("/api/prompts/location/present/versions/99").status_code == 404
    assert client.delete("/api/prompts/location/ghost").status_code == 404
