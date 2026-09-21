"""JSON contract for Prompts (spec §A.5, ticket 05). These are the twins of the Jinja
``/prompts`` list, its authoring form, the version-history page, and its "edit" form: the
flat family list (ADR 0032 — no task groups them), creating a new family's v1, browsing a
family's immutable version history newest-first with each version's text (so compare needs
no extra round-trip), and appending the next immutable version. The shipped prompt is
seeded on app startup, so the listing always holds a ``default`` family.

Deleting a prompt at either granularity (ticket 09, ADR-0016) is covered at the bottom: the
DELETE routes cascade the pinning Runs and return the collateral counts, and the history
endpoint carries those counts up front so the confirm dialog can state the blast radius.
"""

import pytest
from sqlmodel import Session, select

from api.deps import get_prompt_service
from core.models.drawing import Drawing
from core.models.prompt import Prompt
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
            prompt_id=prompt_id,
            drawing_id=drawing.id,
            dpi=200,
            downsample_px=1600,
            max_tokens=4096,
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
        service.create(family, "v1")
        for _ in range(2, n_versions + 1):
            service.edit(family, "next")
        return [p.id for p in service.history(family)[::-1]]


@pytest.fixture
def delete_capable_prompts(app, engine, tmp_path):
    """Override the prompt routes' service so its delete cascade cleans overlay files under a
    temp root rather than the real data root (mirrors the Drawing delete's override)."""

    def _svc():
        with Session(engine) as session:
            yield PromptService(session, overlay_root=tmp_path / "overlays")

    app.dependency_overrides[get_prompt_service] = _svc
    return _svc


def test_list_returns_a_flat_family_collection(client):
    body = client.get("/api/prompts").json()

    # A bare ``families`` collection — no per-task wrapper and no task key anywhere
    # (ADR 0032).
    assert set(body) == {"families"}
    default = next(f for f in body["families"] if f["name"] == "default")
    assert default == {"name": "default", "latest_version": 1, "count": 1}


def test_create_makes_v1_and_lists_it(client):
    resp = client.post("/api/prompts", json={"family": "kitchen", "text": "find them"})
    assert resp.status_code == 201
    assert resp.json() == {"family": "kitchen", "version": 1}

    families = client.get("/api/prompts").json()["families"]
    kitchen = next(f for f in families if f["name"] == "kitchen")
    assert kitchen == {"name": "kitchen", "latest_version": 1, "count": 1}


def test_create_duplicate_family_is_400_with_service_message(client):
    client.post("/api/prompts", json={"family": "dupe", "text": "a"})
    again = client.post("/api/prompts", json={"family": "dupe", "text": "b"})
    assert again.status_code == 400
    assert "already exists" in again.json()["detail"]


def test_history_is_newest_first_with_each_versions_text(client):
    client.post("/api/prompts", json={"family": "doors", "text": "locate v1"})
    client.post("/api/prompts/doors/versions", json={"text": "locate v2"})

    body = client.get("/api/prompts/doors").json()
    assert body["family"] == "doors"
    # Newest-first, and each version carries its text so compare needs no follow-up fetch.
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert [v["text"] for v in body["versions"]] == ["locate v2", "locate v1"]
    assert all(v["created_at"] for v in body["versions"])


def test_the_old_task_segment_path_does_not_resolve(client):
    """A stale client asking at ``/prompts/<task>/<family>`` gets a 404 rather than a
    family that happens to be named after a task (ADR 0032: retired paths fail loudly).
    """
    client.post("/api/prompts", json={"family": "doors", "text": "locate v1"})

    assert client.get("/api/prompts/location/doors").status_code == 404


def test_append_version_never_mutates_prior_versions(client):
    client.post("/api/prompts", json={"family": "cab", "text": "v1 text"})
    resp = client.post("/api/prompts/cab/versions", json={"text": "v2 text"})
    assert resp.status_code == 201
    assert resp.json() == {"family": "cab", "version": 2}

    versions = {
        v["version"]: v["text"]
        for v in client.get("/api/prompts/cab").json()["versions"]
    }
    # The v1 row is untouched by the edit (immutable append, ADR 0009).
    assert versions == {1: "v1 text", 2: "v2 text"}


def test_history_unknown_family_is_404(client):
    assert client.get("/api/prompts/ghost").status_code == 404


def test_append_to_unknown_family_is_404(client):
    resp = client.post("/api/prompts/ghost/versions", json={"text": "x"})
    assert resp.status_code == 404


def test_history_reports_delete_collateral_counts(client, engine):
    v1_id, v2_id = _author_family(engine, "cabinets", 2)
    _pin_run(engine, v1_id)  # one Run pins v1
    _pin_run(engine, v2_id)  # two Runs pin v2
    _pin_run(engine, v2_id)

    body = client.get("/api/prompts/cabinets").json()

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

    resp = client.delete("/api/prompts/doors/versions/1")
    assert resp.status_code == 200
    assert resp.json() == {"runs": 1, "results": 1}

    # v1 is gone from the history (now just v2) and its Run with it; v2's Run survives.
    versions = [
        v["version"] for v in client.get("/api/prompts/doors").json()["versions"]
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

    resp = client.delete("/api/prompts/windows")
    assert resp.status_code == 200
    assert resp.json() == {"runs": 2, "results": 2}

    # The family no longer resolves, and no Run is left behind.
    assert client.get("/api/prompts/windows").status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Run)).all() == []
        assert (
            session.exec(select(Prompt).where(Prompt.family == "windows")).all() == []
        )


def test_delete_unknown_version_and_family_are_404(client, delete_capable_prompts):
    authored = client.post("/api/prompts", json={"family": "present", "text": "v1"})
    assert authored.status_code == 201
    assert client.delete("/api/prompts/present/versions/99").status_code == 404
    assert client.delete("/api/prompts/ghost").status_code == 404
