"""Background-run execution tests (ticket 06, ADR 0006).

The primary seam is unchanged from ticket 05 — the OpenRouter adapter is stubbed so
no network is hit — but execution now happens on an in-process background task:
``BackgroundRunner`` fans models out in parallel under a bounded concurrency cap with
pages sequential per model, advancing ``status`` (queued → running → done/failed) and
a ``progress`` counter. Tests ``submit`` a Run and join the worker thread to observe
the terminal state deterministically.
"""

import threading
import time
from pathlib import Path

from conftest import seed_page_images
from sqlmodel import Session, select

from core.models.drawing import Drawing, Page
from core.models.prompt import Task
from core.models.run import Prediction, PredictionStatus, RunStatus
from core.services.prompt import PromptService
from core.services.run import BackgroundRunner, RunService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"
GEMINI = "google/gemini-2.5-flash"

COUNT_JSON = (
    '{"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)


def _seed_drawing(session, n_pages: int) -> Drawing:
    data_dir = Path(session.get_bind().url.database).parent
    drawing = Drawing(name="sample")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    # Real native rasters under the temp data dir so render-on-demand has an image per page.
    images = seed_page_images(data_dir / "drawings" / str(drawing.id), n_pages)
    for page_number, image in enumerate(images, start=1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=str(image),
                width_px=100,
                height_px=100,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def _seed_prompt(session):
    return PromptService(session).create(Task.counting, "default", "count them")


def test_create_run_is_queued_and_returns_before_execution(session, stub_adapter):
    """create_run returns a ``queued`` Run with its Results but no Predictions yet —
    submission is what starts the work (spec: submitting returns immediately)."""
    drawing = _seed_drawing(session, n_pages=2)
    prompt = _seed_prompt(session)

    run = RunService(session, stub_adapter).create_run(
        Task.counting, prompt.id, drawing.id, [SONNET, GPT]
    )

    assert run.status == RunStatus.queued
    assert run.progress == 0
    assert run.total_units == 4
    assert {r.model for r in run.results} == {SONNET, GPT}
    assert session.exec(select(Prediction)).all() == []


def test_background_run_reaches_done_with_progress_and_predictions(
    engine, stub_adapter
):
    """The ticket's headline test: a stubbed multi-model Run reaches ``done`` with
    progress fully advanced and every (model, page) Prediction persisted."""
    with Session(engine) as session:
        drawing = _seed_drawing(session, n_pages=2)
        prompt = _seed_prompt(session)
        stub_adapter.responses = {SONNET: COUNT_JSON, GPT: COUNT_JSON}
        run = RunService(session, stub_adapter).create_run(
            Task.counting, prompt.id, drawing.id, [SONNET, GPT]
        )
        run_id = run.id

    BackgroundRunner(engine, stub_adapter).submit(run_id).join(timeout=10)

    with Session(engine) as session:
        run = _get_run(session, run_id)
        assert run.status == RunStatus.done
        assert run.progress == run.total_units == 4
        preds = session.exec(select(Prediction)).all()
        assert len(preds) == 4
        assert all(p.status == PredictionStatus.ok for p in preds)


def test_models_run_in_parallel_with_pages_sequential(engine):
    """Models fan out in parallel up to the cap; within a model, pages never overlap
    (spec: bounded concurrency, pages sequential per model)."""
    with Session(engine) as session:
        drawing = _seed_drawing(session, n_pages=3)
        prompt = _seed_prompt(session)
        run = RunService(session, _ConcurrencyAdapter()).create_run(
            Task.counting, prompt.id, drawing.id, [SONNET, GPT, GEMINI]
        )
        run_id = run.id

    adapter = _ConcurrencyAdapter(delay=0.02)
    BackgroundRunner(engine, adapter, max_workers=2).submit(run_id).join(timeout=30)

    with Session(engine) as session:
        assert _get_run(session, run_id).status == RunStatus.done
    # Two models were allowed to run at once but never a third (cap honoured)...
    assert adapter.max_concurrent == 2
    # ...and no single model ever had two of its pages in flight at once.
    assert adapter.max_concurrent_per_model == 1


def test_model_error_recorded_without_losing_other_models(engine):
    """A model whose call raises is recorded as a failure Prediction; the Run still
    reaches ``done`` and other models' Predictions survive (spec: criterion 5)."""
    with Session(engine) as session:
        drawing = _seed_drawing(session, n_pages=1)
        prompt = _seed_prompt(session)
        run = RunService(session, _ConcurrencyAdapter()).create_run(
            Task.counting, prompt.id, drawing.id, [SONNET, GPT]
        )
        run_id = run.id

    adapter = _RaisingAdapter(fail_model=GPT, good_content=COUNT_JSON)
    BackgroundRunner(engine, adapter).submit(run_id).join(timeout=10)

    with Session(engine) as session:
        run = _get_run(session, run_id)
        assert run.status == RunStatus.done
        assert run.progress == run.total_units == 2
        by_model = {r.model: r.predictions for r in run.results}
        assert [p.status for p in by_model[SONNET]] == [PredictionStatus.ok]
        bad = by_model[GPT]
        assert len(bad) == 1
        assert bad[0].status == PredictionStatus.error
        assert "boom" in bad[0].parse_error


def _get_run(session, run_id):
    from core.models.run import Run

    return session.get(Run, run_id)


class _ConcurrencyAdapter:
    """Stub that records the maximum number of overlapping calls, both overall and
    within a single model, so tests can assert on the fan-out shape."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self._lock = threading.Lock()
        self._active = 0
        self._active_by_model: dict[str, int] = {}
        self.max_concurrent = 0
        self.max_concurrent_per_model = 0

    def send_image_prompt(self, image_path, model, prompt, **kwargs) -> dict:
        with self._lock:
            self._active += 1
            self._active_by_model[model] = self._active_by_model.get(model, 0) + 1
            self.max_concurrent = max(self.max_concurrent, self._active)
            self.max_concurrent_per_model = max(
                self.max_concurrent_per_model, self._active_by_model[model]
            )
        time.sleep(self.delay)
        with self._lock:
            self._active -= 1
            self._active_by_model[model] -= 1
        return {"choices": [{"message": {"content": COUNT_JSON}}]}


class _RaisingAdapter:
    """Returns canned JSON for every model except ``fail_model``, which raises."""

    def __init__(self, fail_model: str, good_content: str):
        self.fail_model = fail_model
        self.good_content = good_content

    def send_image_prompt(self, image_path, model, prompt, **kwargs) -> dict:
        if model == self.fail_model:
            raise RuntimeError("boom: openrouter unreachable")
        return {"choices": [{"message": {"content": self.good_content}}]}
