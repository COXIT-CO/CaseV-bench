"""Run execution — the shared service that launches a Run and persists its Results +
Predictions (spec: Runs & execution; tickets 05, 06, 09).

Two pieces live here around the per-page predict routine (``predict_location``, the single
primary test seam — it takes an ``OpenRouterAdapter`` so tests stub the only external I/O
boundary):

- ``RunService`` inserts a ``queued`` Run with one Result per model and the knob
  snapshot (``create_run``). This is what a request calls synchronously and returns.
- ``BackgroundRunner`` runs that queued Run on an **in-process background task**
  (ADR 0006): it advances ``status`` (queued → running → done/failed) and a
  ``progress`` counter, fanning models out in parallel under a bounded concurrency
  cap while pages run **sequentially per model**. The HTMX frontend polls a status
  endpoint and swaps in results when finished. A Run that reaches ``done`` then offers
  its scores to the shared results store (``services.results_store``, scope 9 ticket 05)
  — best-effort, after the commit, and incapable of failing the Run.

Each model becomes a ``Result``; every Page becomes a ``Prediction`` under it — the
labeled boxes parsed from the model's JSON plus a prediction-overlay PNG drawn on the
page image (ticket 09) — or a failure record. The request is a single model-agnostic user
turn (no prefill; ADR 0019) and the response is parsed, retried once before a failure is
recorded; a model failure never aborts the Run (spec: Runs 20, 21).
"""

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
from core.models.prompt import Prompt
from core.models.results import LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from core.services.deletion import RunCascadeCounts, cascade_delete_runs
from core.services.pdf_processing import DEFAULT_DPI, render_run_page
from core.services.results_store import ResultsStoreConfig, publish_completed_run
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
    downsample_px: int | None = DEFAULT_DOWNSAMPLE_PX
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float | None = DEFAULT_TEMPERATURE


class _PageRef(NamedTuple):
    """A page's fields a worker needs, snapshotted up front so background threads never touch
    a shared (session-bound) ORM object. ``drawing_dir`` (the Page image's directory) and the
    Drawing's ``source_path`` are what render-on-demand needs to produce the ``(dpi,
    downsample)`` variant handed to the Model (ADR 0018)."""

    id: int
    page_number: int
    drawing_dir: str
    source_path: str | None


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
        results_store: ResultsStoreConfig | None = None,
    ):
        self.session = session
        self.adapter = adapter
        self.knobs = knobs
        self.overlay_root = overlay_root
        self.results_store = results_store

    def create_run(self, prompt_id: int, drawing_id: int, models: list[str]) -> Run:
        """Insert a ``queued`` Run with one Result per model and the knob snapshot."""
        if self.session.get(Prompt, prompt_id) is None:
            raise ValueError(f"no prompt with id {prompt_id}")
        drawing = self.session.get(Drawing, drawing_id)
        if drawing is None:
            raise ValueError(f"no drawing with id {drawing_id}")
        if not models:
            raise ValueError("a Run needs at least one model")

        run = Run(
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
        """A ``BackgroundRunner`` sharing this service's adapter, knob snapshot and
        results-store destination, so the background execution path can't drift from what
        ``create_run`` recorded — nor publish somewhere this service was not pointed at.
        The runner needs the engine (not the request-bound session) to open a fresh
        session per worker thread (ADR 0006)."""
        return BackgroundRunner(
            engine,
            self.adapter,
            self.knobs,
            overlay_root=self.overlay_root,
            results_store=self.results_store,
        )

    def launch(self, prompt_id: int, drawing_id: int, models: list[str]) -> Run:
        """Create a Run and run it to completion, blocking until done — a synchronous
        convenience for the CLI and tests. The web launch path instead calls
        ``create_run`` and hands the id to a ``BackgroundRunner`` so the request
        returns while the work continues (ADR 0006)."""
        run = self.create_run(prompt_id, drawing_id, models)
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
        results_store: ResultsStoreConfig | None = None,
    ):
        self.engine = engine
        self.adapter = adapter
        self.knobs = knobs
        self.max_workers = max_workers
        self.overlay_root = overlay_root
        # Where a finished Run's scores are published (scope 9, ticket 05). ``None`` means
        # "whatever the environment configures", which is normally nothing at all — tests
        # inject a recorder. Publishing is best-effort by construction and cannot fail a Run.
        self.results_store = results_store
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
        (a *model* failure is recorded as a Prediction and never fails the Run). A ``done``
        Run's scores are then published to the shared results store, if one is configured.
        """
        with Session(self.engine) as session:
            run = session.get(Run, run_id)
            run.status = RunStatus.running
            session.commit()

            prompt_text = session.get(Prompt, run.prompt_id).text
            drawing = session.get(Drawing, run.drawing_id)
            pages = [
                _PageRef(
                    page.id,
                    page.page_number,
                    str(Path(page.image_path).parent),
                    drawing.source_path,
                )
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
            # Last, and after the commit: the Run is complete and durable before its scores
            # are offered to the shared store, so a store outage costs the record of an
            # experiment and never the experiment itself (scope 9, ticket 05).
            publish_completed_run(session, run, self.results_store)

    def _execute_result(
        self,
        run_id: int,
        result_id: int,
        model: str,
        pages: list[_PageRef],
        prompt_text: str,
    ) -> None:
        """One model's Result: walk its pages **sequentially**, persisting a Prediction
        and advancing progress after each. A model failure is already captured inside
        the predict routine as a failure Prediction, so this only raises on a real
        persistence error — which marks the whole Run ``failed``."""
        for page in pages:
            prediction = predict_location(
                self.adapter,
                result_id,
                model,
                page,
                prompt_text,
                self.knobs,
                self.overlay_root,
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


def _render_page(page: _PageRef, knobs: RunKnobs) -> Path:
    """The image handed to the Model for this page: rendered on demand at the Run's ``(dpi,
    downsample_px)`` from the retained PDF (or the native raster for an image Drawing), cached
    under the drawing dir (ADR 0018). This is what makes DPI/downsample genuinely effective —
    the Model no longer sees the fixed ingest downsample."""
    return render_run_page(
        Path(page.drawing_dir),
        page.page_number,
        page.source_path,
        knobs.dpi,
        knobs.downsample_px,
    )


def _predict(
    adapter: OpenRouterAdapter,
    model: str,
    image_path: Path,
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
                image_path,
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
    cleanly_recovered = salvaged.complete and not dropped
    # A legitimately empty array (cleanly recovered, nothing dropped) means the model found no
    # objects on the page — a valid answer, not a failure. Emptiness from dropped boxes or an
    # incomplete recovery is an error.
    if not detections and not cleanly_recovered:
        error = salvaged.error or "no valid boxes in response"
        return _Interpretation(clean=False, error=error)

    parsed_json = LocationResult(detections=detections).model_dump_json()
    if cleanly_recovered:
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
    else an ``error`` that still retains the salvaged ``parsed_json``/overlay — those salvaged
    boxes are still scored (ADR 0027), so ``error`` is a data-quality flag, not an unscored
    verdict. ``raw_content`` is retained either way (spec: Runs 19, 20)."""
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
    image_path = _render_page(page, knobs)
    raw_content, interp = _predict(
        adapter, model, image_path, prompt_text, knobs, _interpret_location
    )

    overlay_path: str | None = None
    # Render whenever the parse was clean (an ``ok``) or any box survived a salvage — the same
    # set the UI treats as viewable. A clean but *empty* answer ("no objects here") still gets
    # the bare page image so the drill-down shows the page, not a broken image; only a total
    # parse failure (error, no boxes) stays overlay-less and falls back to the placeholder.
    if interp.clean or interp.detections:
        path = overlay_root / str(result_id) / f"page_{page.page_number:04d}.png"
        # Draw on the same image the Model saw so the boxes land on the rendered variant.
        draw_overlay(image_path, interp.detections, path)
        overlay_path = str(path)
    return _prediction(page, result_id, raw_content, interp, overlay_path)
