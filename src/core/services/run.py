"""Run execution — the shared service that launches a Run and persists its Results +
Predictions (spec: Runs & execution; tickets 05, 06, 09).

Two pieces live here around the per-page predict routines (``predict_counting`` /
``predict_location``, the single primary test seam — they take an ``OpenRouterAdapter``
so tests stub the only external I/O boundary):

- ``RunService`` inserts a ``queued`` Run with one Result per model and the knob
  snapshot (``create_run``). This is what a request calls synchronously and returns.
- ``BackgroundRunner`` runs that queued Run on an **in-process background task**
  (ADR 0006): it advances ``status`` (queued → running → done/failed) and a
  ``progress`` counter, fanning models out in parallel under a bounded concurrency
  cap while pages run **sequentially per model**. The HTMX frontend polls a status
  endpoint and swaps in results when finished.

Each model becomes a ``Result``; every Page becomes a ``Prediction`` under it — for
counting the per-page counts parsed from the model's JSON; for location the labeled
boxes plus a prediction-overlay PNG drawn on the page image (ticket 09) — or a failure
record. JSON is obtained via the existing prefill + strip-fence approach and retried
once before a failure is recorded; a model failure never aborts the Run (spec: Runs
20, 21).
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import Engine
from sqlmodel import Session, select

from core.adapters.openrouter import DEFAULT_MAX_TOKENS, OpenRouterAdapter
from core.config import settings
from core.models.drawing import Drawing
from core.models.prompt import Prompt, Task
from core.models.results import CountResult, LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from core.services.pdf_processing import DEFAULT_DPI
from core.utils import DEFAULT_DOWNSAMPLE_PX, draw_overlay, parse_json

# A Run pins a fixed temperature for reproducibility (spec: knobs recorded but fixed).
DEFAULT_TEMPERATURE = 0.0

# How many models may call OpenRouter at once. Bounded so a 3-model run finishes ~3×
# faster than fully sequential without hammering rate limits (ADR 0006).
DEFAULT_MAX_CONCURRENCY = 3

# Where location prediction-overlay PNGs are cached, keyed by Result then page number
# (ticket 09). A dedicated root (not the page-image dir) since many Results share a Page.
# Production default under the single data root; tests inject a temp root (ADR-0014).
DEFAULT_OVERLAY_ROOT = settings.overlays_root

# Tasks the run path can execute today (counting: ticket 05/06; location: ticket 09).
SUPPORTED_TASKS = (Task.counting, Task.location)


def reconcile_orphaned_runs(session: Session) -> int:
    """Sweep any Run left ``running`` by an interrupted process to ``failed`` (ticket 04).

    Background runs execute on an in-process thread (ADR 0006), so they die with the
    process that hosts them — e.g. a Railway redeploy kills the old container mid-run
    (ADR 0013). The Run row is then stuck ``running`` with no thread left to finish it,
    and the Leaderboard would show a zombie run that can never complete. Startup is a safe
    moment to reconcile: a freshly booted process has nothing legitimately in flight, so
    every ``running`` Run is an orphan of a prior process. Runs already in a terminal state
    (``done``, ``failed``) — and ones still merely ``queued`` — are left untouched. Returns
    the number of Runs swept.
    """
    orphaned = session.exec(select(Run).where(Run.status == RunStatus.running)).all()
    for run in orphaned:
        run.status = RunStatus.failed
    session.commit()
    return len(orphaned)


@dataclass(frozen=True)
class RunKnobs:
    """The fixed, non-tunable knobs a Run snapshots (glossary: Configuration is
    ``(prompt version, model)`` only; everything here is recorded but fixed in v1).
    Field names match the ``Run`` columns so the snapshot copies by ``asdict``."""

    dpi: int = DEFAULT_DPI
    downsample_px: int = DEFAULT_DOWNSAMPLE_PX
    max_tokens: int = DEFAULT_MAX_TOKENS
    prefill: bool = True
    temperature: float = DEFAULT_TEMPERATURE


class _PageRef(NamedTuple):
    """A page's fields a worker needs, snapshotted up front so background threads
    never touch a shared (session-bound) ORM object."""

    id: int
    page_number: int
    image_path: str


class RunService:
    """Session-scoped creation of a Run. Execution is a separate concern
    (``BackgroundRunner``) so a request can insert the ``queued`` Run and return
    immediately (spec: submitting a Run returns immediately)."""

    def __init__(
        self,
        session: Session,
        adapter: OpenRouterAdapter,
        knobs: RunKnobs = RunKnobs(),
        overlay_root: Path = DEFAULT_OVERLAY_ROOT,
    ):
        self.session = session
        self.adapter = adapter
        self.knobs = knobs
        self.overlay_root = overlay_root

    def create_run(
        self, task: Task, prompt_id: int, drawing_id: int, models: list[str]
    ) -> Run:
        """Insert a ``queued`` Run with one Result per model and the knob snapshot."""
        if task not in SUPPORTED_TASKS:
            raise ValueError(f"unsupported Run task {task.value}")
        prompt = self.session.get(Prompt, prompt_id)
        if prompt is None or prompt.task != task:
            raise ValueError(f"no {task.value} prompt with id {prompt_id}")
        drawing = self.session.get(Drawing, drawing_id)
        if drawing is None:
            raise ValueError(f"no drawing with id {drawing_id}")
        if not models:
            raise ValueError("a Run needs at least one model")

        run = Run(
            task=task,
            prompt_id=prompt_id,
            drawing_id=drawing_id,
            status=RunStatus.queued,
            total_units=len(models) * len(drawing.pages),
            **asdict(self.knobs),
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        for model in models:
            self.session.add(Result(run_id=run.id, model=model))
        self.session.commit()
        self.session.refresh(run)
        return run

    def background_runner(self, engine: Engine) -> "BackgroundRunner":
        """A ``BackgroundRunner`` sharing this service's adapter and knob snapshot, so
        the background execution path can't drift from what ``create_run`` recorded.
        The runner needs the engine (not the request-bound session) to open a fresh
        session per worker thread (ADR 0006)."""
        return BackgroundRunner(
            engine, self.adapter, self.knobs, overlay_root=self.overlay_root
        )

    def launch(
        self, task: Task, prompt_id: int, drawing_id: int, models: list[str]
    ) -> Run:
        """Create a Run and run it to completion, blocking until done — a synchronous
        convenience for the CLI and tests. The web launch path instead calls
        ``create_run`` and hands the id to a ``BackgroundRunner`` so the request
        returns while the work continues (ADR 0006)."""
        run = self.create_run(task, prompt_id, drawing_id, models)
        self.background_runner(self.session.get_bind()).execute_run(run.id)
        self.session.refresh(run)
        return run


class BackgroundRunner:
    """Executes a queued Run on an in-process background task (ADR 0006).

    ``submit`` starts a daemon thread and returns immediately; ``execute_run`` is the
    body (also callable directly for a deterministic synchronous run). Models fan out
    through a bounded ``ThreadPoolExecutor`` so at most ``max_workers`` call OpenRouter
    at once; within a model, pages run sequentially. The slow work (the OpenRouter
    call) happens off-lock in parallel, while the DB writes for a page's Prediction
    and the ``progress`` bump are serialized under a per-instance lock so this Run's
    own workers never collide on SQLite (a single writer). Two Runs launched at once
    hold separate locks; their brief write overlap is absorbed by the sqlite busy
    timeout (db.py), not this lock — acceptable for a local 5-dev tool (ADR 0006)."""

    def __init__(
        self,
        engine: Engine,
        adapter: OpenRouterAdapter,
        knobs: RunKnobs = RunKnobs(),
        max_workers: int = DEFAULT_MAX_CONCURRENCY,
        overlay_root: Path = DEFAULT_OVERLAY_ROOT,
    ):
        self.engine = engine
        self.adapter = adapter
        self.knobs = knobs
        self.max_workers = max_workers
        self.overlay_root = overlay_root
        self._write_lock = threading.Lock()

    def submit(self, run_id: int) -> threading.Thread:
        """Start executing the Run in the background and return the thread at once, so
        the caller (a request) doesn't block on the fan-out."""
        thread = threading.Thread(target=self.execute_run, args=(run_id,), daemon=True)
        thread.start()
        return thread

    def execute_run(self, run_id: int) -> None:
        """Drive one Run to a terminal state. Snapshots what workers need up front,
        marks the Run ``running``, fans the models out under the concurrency cap, then
        records ``done`` — or ``failed`` if a worker hit a genuine persistence error
        (a *model* failure is recorded as a Prediction and never fails the Run)."""
        with Session(self.engine) as session:
            run = session.get(Run, run_id)
            run.status = RunStatus.running
            session.commit()

            task = run.task
            prompt_text = session.get(Prompt, run.prompt_id).text
            drawing = session.get(Drawing, run.drawing_id)
            pages = [
                _PageRef(page.id, page.page_number, page.image_path)
                for page in drawing.pages
            ]
            results = [(result.id, result.model) for result in run.results]

        errors: list[BaseException] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [
                pool.submit(
                    self._execute_result,
                    run_id,
                    result_id,
                    model,
                    pages,
                    prompt_text,
                    task,
                )
                for result_id, model in results
            ]
            for future in futures:
                exc = future.exception()
                if exc is not None:
                    errors.append(exc)

        with Session(self.engine) as session:
            run = session.get(Run, run_id)
            run.status = RunStatus.failed if errors else RunStatus.done
            session.commit()

    def _execute_result(
        self,
        run_id: int,
        result_id: int,
        model: str,
        pages: list[_PageRef],
        prompt_text: str,
        task: Task,
    ) -> None:
        """One model's Result: walk its pages **sequentially**, persisting a Prediction
        and advancing progress after each. A model failure is already captured inside
        the predict routine as a failure Prediction, so this only raises on a real
        persistence error — which marks the whole Run ``failed``."""
        for page in pages:
            if task == Task.location:
                prediction = predict_location(
                    self.adapter,
                    result_id,
                    model,
                    page,
                    prompt_text,
                    self.knobs,
                    self.overlay_root,
                )
            else:
                prediction = predict_counting(
                    self.adapter, result_id, model, page, prompt_text, self.knobs
                )
            with self._write_lock:
                with Session(self.engine) as session:
                    session.add(prediction)
                    run = session.get(Run, run_id)
                    run.progress += 1
                    session.commit()


def _predict_json(
    adapter: OpenRouterAdapter,
    model: str,
    page: _PageRef,
    prompt_text: str,
    knobs: RunKnobs,
    parse: Callable[[str], object],
) -> tuple[str | None, object | None, str | None]:
    """Send the page image + prompt and ``parse`` the response, retried once before
    giving up (spec: Runs 20). A failing OpenRouter call is caught like a parse failure
    rather than propagated, so one model's error doesn't abort the Run (spec: Runs 21).

    Returns ``(raw_content, parsed, error)``: on success ``error`` is None and ``parsed``
    is ``parse``'s output; on failure ``parsed`` is None and ``error`` holds the message
    (with ``raw_content`` from the last attempt for inspection). The single external I/O
    boundary, kept session-free so the predict routines stay a pure test seam."""
    raw_content: str | None = None
    error: str | None = None

    # Initial attempt plus a single retry.
    for _ in range(2):
        try:
            response = adapter.send_image_prompt(
                Path(page.image_path),
                model,
                prompt_text,
                prefill_json=knobs.prefill,
                max_tokens=knobs.max_tokens,
                temperature=knobs.temperature,
            )
            raw_content = response["choices"][0]["message"]["content"]
            return raw_content, parse(raw_content), None
        except Exception as exc:
            error = str(exc)

    return raw_content, None, error


def _failure(
    page: _PageRef, result_id: int, raw_content: str | None, error: str | None
) -> Prediction:
    return Prediction(
        result_id=result_id,
        page_id=page.id,
        page_number=page.page_number,
        status=PredictionStatus.error,
        raw_content=raw_content,
        parse_error=error,
    )


def _success(
    page: _PageRef,
    result_id: int,
    raw_content: str | None,
    parsed_json: str,
    overlay_path: str | None = None,
) -> Prediction:
    return Prediction(
        result_id=result_id,
        page_id=page.id,
        page_number=page.page_number,
        status=PredictionStatus.ok,
        raw_content=raw_content,
        parsed_json=parsed_json,
        overlay_path=overlay_path,
    )


def predict_counting(
    adapter: OpenRouterAdapter,
    result_id: int,
    model: str,
    page: _PageRef,
    prompt_text: str,
    knobs: RunKnobs,
) -> Prediction:
    """One page's counting Prediction: prefill + strip-fence parse into the per-page
    counts, retried once before recording a failure (spec: Runs 20, 21). Returns an
    unsaved ``Prediction`` — persistence is the caller's, kept out of this routine so it
    stays a pure, session-free seam."""
    raw_content, counts, error = _predict_json(
        adapter, model, page, prompt_text, knobs, lambda c: CountResult(**parse_json(c))
    )
    if error is not None:
        return _failure(page, result_id, raw_content, error)
    return _success(page, result_id, raw_content, counts.model_dump_json())


def predict_location(
    adapter: OpenRouterAdapter,
    result_id: int,
    model: str,
    page: _PageRef,
    prompt_text: str,
    knobs: RunKnobs,
    overlay_root: Path,
) -> Prediction:
    """One page's location Prediction: parse the model's JSON list of labeled boxes,
    retried once before recording a failure (spec: Runs 20, 21). On success the detected
    boxes are stored as a ``LocationResult`` and a prediction-overlay PNG is rendered on
    the page image via the shared ``draw_overlay`` (ticket 09), its path stored on the
    Prediction. Returns an unsaved ``Prediction`` — DB persistence is the caller's."""
    raw_content, detections, error = _predict_json(
        adapter,
        model,
        page,
        prompt_text,
        knobs,
        lambda c: [LocationDetection(**box) for box in parse_json(c)],
    )
    if error is not None:
        return _failure(page, result_id, raw_content, error)

    overlay_path = overlay_root / str(result_id) / f"page_{page.page_number:04d}.png"
    draw_overlay(Path(page.image_path), detections, overlay_path)
    return _success(
        page,
        result_id,
        raw_content,
        LocationResult(detections=detections).model_dump_json(),
        str(overlay_path),
    )
