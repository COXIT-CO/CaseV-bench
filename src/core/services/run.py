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
record. The request is a single model-agnostic user turn (no prefill; ADR 0019) and the
response is parsed, retried once before a failure is recorded; a model failure never
aborts the Run (spec: Runs 20, 21).
"""

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlmodel import Session, select

from core.adapters.openrouter import DEFAULT_MAX_TOKENS, OpenRouterAdapter
from core.config import settings
from core.models.drawing import Drawing
from core.models.prompt import Prompt, Task
from core.models.results import CountResult, LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from core.services.deletion import RunCascadeCounts, cascade_delete_runs
from core.services.pdf_processing import DEFAULT_DPI
from core.utils import DEFAULT_DOWNSAMPLE_PX, draw_overlay, salvage_json

# The default temperature a Run pins for reproducibility; a Run may instead set None to
# run under the provider default, which is omitted from the request payload (ADR 0018/0019).
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
    """The per-run knobs a Run snapshots — recorded and displayed, but not a Leaderboard
    rank axis (Configuration stays ``(prompt version, model)``; ADR 0018). ``temperature``
    of ``None`` means "provider default" and is omitted from the request payload, so one
    config runs reasoning and older models identically (ADR 0019). Field names match the
    ``Run`` columns so the snapshot copies by ``asdict``."""

    dpi: int = DEFAULT_DPI
    downsample_px: int = DEFAULT_DOWNSAMPLE_PX
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float | None = DEFAULT_TEMPERATURE


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

    def delete_run(self, run_id: int) -> RunCascadeCounts:
        """Permanently delete a Run and everything under it — its Results, Predictions,
        Scores, and cached overlay files — returning the collateral counts the confirm
        dialog showed (ADR-0016). The Run then vanishes from the Leaderboard and the run
        history. Raises ``ValueError`` when there is no such Run so the route can 404.
        """
        if self.session.get(Run, run_id) is None:
            raise ValueError(f"no run with id {run_id}")
        return cascade_delete_runs(self.session, [run_id], self.overlay_root)

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


@dataclass(frozen=True)
class _Interpretation:
    """One attempt's parse+validate outcome (ADR 0019). ``clean`` — the whole response was
    recovered and validated — gates a scored ``ok``; anything else stays an ``error`` that
    still stores ``parsed_json`` (best-effort salvage, for display) and, for location, the
    ``detections`` to draw. ``error`` is the failure/salvage message; None only when clean.
    """

    clean: bool
    parsed_json: str | None = None
    detections: list[LocationDetection] = field(default_factory=list)
    error: str | None = None


def _predict(
    adapter: OpenRouterAdapter,
    model: str,
    page: _PageRef,
    prompt_text: str,
    knobs: RunKnobs,
    interpret: Callable[[str], _Interpretation],
) -> tuple[str | None, _Interpretation]:
    """Send the page image + prompt and ``interpret`` the response, retried once whenever
    the first parse isn't cleanly ``ok`` (spec: Runs 20; ADR 0019). A failing OpenRouter
    call is caught like a parse failure rather than propagated, so one model's error doesn't
    abort the Run (spec: Runs 21). ``interpret`` never raises — a malformed body becomes a
    non-clean ``_Interpretation`` whose salvage is shown for display.

    Returns ``(raw_content, interpretation)`` from the clean attempt, else from the last
    attempt (retaining that attempt's ``raw_content`` for inspection). The single external
    I/O boundary, kept session-free so the predict routines stay a pure test seam."""
    raw_content: str | None = None
    interp = _Interpretation(clean=False, error="model produced no response")

    # Initial attempt plus a single retry; a clean parse short-circuits the retry.
    for _ in range(2):
        try:
            response = adapter.send_image_prompt(
                Path(page.image_path),
                model,
                prompt_text,
                max_tokens=knobs.max_tokens,
                temperature=knobs.temperature,
            )
            raw = response["choices"][0]["message"]["content"]
        except Exception as exc:
            # A raised retry must not discard a salvage the first attempt already produced:
            # only record the error when no earlier attempt returned content, so ``interp``
            # stays paired with ``raw_content`` and the best salvage survives (ADR 0019).
            if raw_content is None:
                interp = _Interpretation(clean=False, error=str(exc))
            continue
        raw_content = raw
        interp = interpret(raw)
        if interp.clean:
            break

    return raw_content, interp


def _interpret_counting(raw: str) -> _Interpretation:
    """Recover per-page counts from a model response (ADR 0019). A clean, fully-valid
    ``CountResult`` scores ``ok``; a recovered-but-invalid structure (wrong shape, missing
    label) is kept visible in ``parsed_json`` but stays a non-clean ``error``."""
    salvaged = salvage_json(raw)
    if salvaged.value is None:
        return _Interpretation(clean=False, error=salvaged.error)
    try:
        counts = CountResult(**salvaged.value)
    except (TypeError, ValidationError) as exc:
        # Keep the parsed structure visible even though it didn't validate.
        return _Interpretation(
            clean=False,
            parsed_json=json.dumps(salvaged.value),
            error=salvaged.error or f"counts did not match the schema: {exc}",
        )
    return _Interpretation(
        clean=salvaged.complete,
        parsed_json=counts.model_dump_json(),
        error=salvaged.error,
    )


def _interpret_location(raw: str) -> _Interpretation:
    """Recover labeled boxes from a model response, validating **element-by-element** so a
    truncated/partly-corrupt array still yields the boxes that parsed (ADR 0019). Clean only
    when the whole array was recovered and every box validated; otherwise a non-clean
    ``error`` carrying the surviving boxes for the salvage overlay + drill-down."""
    salvaged = salvage_json(raw)
    if not isinstance(salvaged.value, list):
        error = salvaged.error or "location response was not a JSON array"
        return _Interpretation(clean=False, error=error)

    detections: list[LocationDetection] = []
    dropped = False
    for item in salvaged.value:
        try:
            detections.append(LocationDetection(**item))
        except (TypeError, ValidationError):
            dropped = True
    if not detections:
        error = salvaged.error or "no valid boxes in response"
        return _Interpretation(clean=False, error=error)

    parsed_json = LocationResult(detections=detections).model_dump_json()
    if salvaged.complete and not dropped:
        return _Interpretation(
            clean=True, parsed_json=parsed_json, detections=detections
        )
    error = salvaged.error or "some boxes were invalid and dropped"
    return _Interpretation(
        clean=False, parsed_json=parsed_json, detections=detections, error=error
    )


def _prediction(
    page: _PageRef,
    result_id: int,
    raw_content: str | None,
    interp: _Interpretation,
    overlay_path: str | None = None,
) -> Prediction:
    """Build the (unsaved) ``Prediction`` from an interpretation: a scored ``ok`` when clean,
    else an unscored ``error`` that still retains the salvaged ``parsed_json``/overlay for
    display. ``raw_content`` is retained either way (spec: Runs 19, 20)."""
    status = PredictionStatus.ok if interp.clean else PredictionStatus.error
    return Prediction(
        result_id=result_id,
        page_id=page.id,
        page_number=page.page_number,
        status=status,
        raw_content=raw_content,
        parsed_json=interp.parsed_json,
        parse_error=None if interp.clean else interp.error,
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
    """One page's counting Prediction: recover the per-page counts from the model's JSON,
    retried once before recording an outcome (spec: Runs 20, 21; ADR 0019). A clean parse
    scores ``ok``; a malformed one stays an ``error`` (its best-effort salvage kept visible).
    Returns an unsaved ``Prediction`` — persistence is the caller's, kept out of this routine
    so it stays a pure, session-free seam."""
    raw_content, interp = _predict(
        adapter, model, page, prompt_text, knobs, _interpret_counting
    )
    return _prediction(page, result_id, raw_content, interp)


def predict_location(
    adapter: OpenRouterAdapter,
    result_id: int,
    model: str,
    page: _PageRef,
    prompt_text: str,
    knobs: RunKnobs,
    overlay_root: Path,
) -> Prediction:
    """One page's location Prediction: recover the model's JSON list of labeled boxes,
    retried once before recording an outcome (spec: Runs 20, 21; ADR 0019). A clean parse
    stores the boxes as a ``LocationResult`` and scores ``ok``; a truncated/partly-corrupt
    response stays an ``error`` but still stores its salvaged boxes. Either way, whenever any
    boxes survived a prediction-overlay PNG is rendered on the page image via the shared
    ``draw_overlay`` (ticket 09) and its path stored, so the salvage is visible in the
    drill-down. Returns an unsaved ``Prediction`` — DB persistence is the caller's."""
    raw_content, interp = _predict(
        adapter, model, page, prompt_text, knobs, _interpret_location
    )

    overlay_path: str | None = None
    if interp.detections:
        path = overlay_root / str(result_id) / f"page_{page.page_number:04d}.png"
        draw_overlay(Path(page.image_path), interp.detections, path)
        overlay_path = str(path)
    return _prediction(page, result_id, raw_content, interp, overlay_path)
