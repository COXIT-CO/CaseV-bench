"""Orphaned-run reconciliation tests (ticket 04, ADR 0006, ADR 0013).

Background runs execute on an in-process thread, so an interrupted process (e.g. a
redeploy) leaves its Run stuck ``running`` with no thread to finish it. Startup sweeps
those to ``failed`` so the Leaderboard never shows a zombie run that can never complete.
The new seam is ``reconcile_orphaned_runs``; both entry points — the web app's lifespan
and the CLI's ``main`` — call it on startup.
"""

from fastapi.testclient import TestClient
from sqlmodel import Session

from core.models.run import Run, RunStatus
from core.services.run import reconcile_orphaned_runs


def test_running_run_swept_to_failed_terminal_and_queued_untouched(
    session, seed_location_run
):
    """The ticket's headline: a ``running`` orphan becomes ``failed`` while ``done`` /
    ``failed`` runs — and a still-``queued`` one — are left exactly as they were."""
    # FK ids are unenforced under SQLite here, so an orphan needs no Drawing or Prompt.
    running = seed_location_run(
        session, prompt_id=1, drawing_id=1, status=RunStatus.running
    )
    done = seed_location_run(session, prompt_id=1, drawing_id=1, status=RunStatus.done)
    failed = seed_location_run(
        session, prompt_id=1, drawing_id=1, status=RunStatus.failed
    )
    queued = seed_location_run(
        session, prompt_id=1, drawing_id=1, status=RunStatus.queued
    )

    swept = reconcile_orphaned_runs(session)

    assert swept == 1
    assert session.get(Run, running.id).status == RunStatus.failed
    assert session.get(Run, done.id).status == RunStatus.done
    assert session.get(Run, failed.id).status == RunStatus.failed
    assert session.get(Run, queued.id).status == RunStatus.queued


def test_no_orphans_is_a_noop(session, seed_location_run):
    """With nothing ``running``, reconciliation sweeps nothing and touches no rows."""
    done = seed_location_run(session, prompt_id=1, drawing_id=1, status=RunStatus.done)

    assert reconcile_orphaned_runs(session) == 0
    assert session.get(Run, done.id).status == RunStatus.done


def test_web_startup_reconciles_orphaned_run(engine, stub_adapter, seed_location_run):
    """The web app's lifespan runs reconciliation on boot: a run left ``running`` before
    the app starts is ``failed`` by the time it serves requests."""
    with Session(engine) as session:
        orphan_id = seed_location_run(
            session, prompt_id=1, drawing_id=1, status=RunStatus.running
        ).id

    from api.app import create_app
    from core.adapters.openrouter import get_openrouter_adapter

    app = create_app(engine=engine)
    app.dependency_overrides[get_openrouter_adapter] = lambda: stub_adapter
    # Entering the TestClient context triggers the lifespan startup.
    with TestClient(app):
        pass

    with Session(engine) as session:
        assert session.get(Run, orphan_id).status == RunStatus.failed


def test_cli_startup_reconciles_orphaned_run(engine, monkeypatch, seed_location_run):
    """The CLI's ``main`` reconciles on startup too, before doing any run work. The run
    itself is stubbed out so the test exercises only the startup path against the temp
    engine."""
    from core import cli

    with Session(engine) as session:
        orphan_id = seed_location_run(
            session, prompt_id=1, drawing_id=1, status=RunStatus.running
        ).id

    monkeypatch.setattr(cli, "make_engine", lambda: engine)
    # Skip the actual ingest + run; we only care that startup reconciled the orphan.
    monkeypatch.setattr(cli, "execute_cli_run", lambda *a, **k: None)

    cli.main([])

    with Session(engine) as session:
        assert session.get(Run, orphan_id).status == RunStatus.failed
