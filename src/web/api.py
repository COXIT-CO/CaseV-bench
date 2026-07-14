"""JSON API router (ADR 0010), mounted under ``/api`` alongside the untouched Jinja/HTMX
routes so the old app stays live while the React SPA is built slice by slice.

Slice 0 (ticket 01) provides a single proof endpoint, ``GET /api/meta``, that the SPA
shell fetches to verify the Vite -> JSON -> shadcn pipeline end to end. A JSON route and
its HTMX twin call the **same service methods**; the feature endpoints (leaderboard,
results, runs, prompts, library) land in their own later slices (spec Part A).
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, SQLModel, select

from adapters.openrouter import OpenRouterAdapter, get_openrouter_adapter
from db import get_session
from models.drawing import Drawing, Page
from models.location_ground_truth import LocationGroundTruth
from models.prompt import Prompt, Task
from models.results import OBJECT_LABELS, LabeledBox, LocationResult
from models.run import Prediction, PredictionStatus, Result, Run
from services.counting_ground_truth import CountingGroundTruthService
from services.drawing import DrawingService
from services.location_ground_truth import LocationGroundTruthService
from services.model_catalog import ModelCatalogService
from services.prompt import PromptService
from services.run import RunService
from services.scoring import (
    LeaderboardMetric,
    LocationLeaderboardMetric,
    ScoringService,
)
from utils import render_compare_overlay

# Mirrors ``web.app.APP_TITLE`` (app.py stays the composition root; duplicating one
# string avoids an import cycle between the app factory and this router).
APP_TITLE = "Prompt & Config Lab"

api_router = APIRouter(prefix="/api")


class ApiMeta(BaseModel):
    """App-level facts the SPA shell renders to prove it is talking to the live API:
    the app name, the fixed Task/ObjectType taxonomy, and current domain-row counts."""

    app: str
    tasks: list[str]
    labels: list[str]
    drawing_count: int
    run_count: int
    result_count: int


def _count(session: Session, model: type[SQLModel]) -> int:
    """Row count for ``model`` without materializing the rows."""
    return session.exec(select(func.count()).select_from(model)).one()


@api_router.get("/meta", response_model=ApiMeta)
def meta(session: Session = Depends(get_session)) -> ApiMeta:
    return ApiMeta(
        app=APP_TITLE,
        tasks=[t.value for t in Task],
        labels=list(OBJECT_LABELS),
        drawing_count=_count(session, Drawing),
        run_count=_count(session, Run),
        result_count=_count(session, Result),
    )


class LeaderboardDrawing(BaseModel):
    """One entry in the board's Drawing filter dropdown (spec §A.2)."""

    id: int
    name: str
    page_count: int


class LeaderboardRowOut(BaseModel):
    """One Leaderboard line — a prompt-version × model Configuration outcome. Carries the
    counting pair **or** the location rates depending on the board's Task; the unused set
    stays ``null``. Unscored rows (no GT) have ``rank:null`` and null metrics, and the
    service has already pinned them last. ``drawing_id``/``drawing_name`` let the SPA show
    the Drawing column and point an unscored row's CTA at its ground-truth entry (§B.2).
    """

    rank: int | None
    result_id: int
    run_id: int
    model: str
    prompt_family: str
    prompt_version: int
    drawing_id: int
    drawing_name: str
    scored: bool
    # Counting metrics.
    total_absolute_error: int | None = None
    exact_match_count: int | None = None
    # Location metrics.
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None


class LeaderboardResponse(BaseModel):
    """The board plus the filter surface the SPA mirrors into the URL (spec §A.2): the
    resolved Task/Drawing/sort, the Task's valid ``sort`` metrics, the Drawing dropdown,
    the taxonomy size (the exact-match denominator), and the already-ranked rows."""

    task: str
    drawing_id: int | None
    sort: str
    metrics: list[str]
    drawings: list[LeaderboardDrawing]
    label_count: int
    rows: list[LeaderboardRowOut]


def _parse_metric(sort: str | None, metric_enum, default):
    """Resolve a ``?sort=`` value to a member of ``metric_enum``, falling back to
    ``default`` for a missing/unknown metric so a stale URL never 500s (mirrors the Jinja
    route's ``_parse_metric``)."""
    try:
        return metric_enum(sort)
    except ValueError:
        return default


@api_router.get("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(
    task: str = Task.counting.value,
    drawing_id: int | None = None,
    sort: str | None = None,
    session: Session = Depends(get_session),
) -> LeaderboardResponse:
    """JSON twin of the Jinja ``/leaderboard`` (spec §A.2). Same service methods, so the
    board is ranked once in the service (``_rank`` / ``_rank_location``) and returned
    already ordered — the SPA renders, it does not re-rank. The Task selects both the
    ranking metrics and the row's score shape; an unknown ``sort`` falls back to the
    Task default. ``rank`` is the 1-based index over scored rows so the SPA needn't
    re-derive it."""
    scoring = ScoringService(session)
    board_task = Task.location if task == Task.location.value else Task.counting

    if board_task == Task.location:
        metric_enum, default = LocationLeaderboardMetric, LocationLeaderboardMetric.f1
        metric = _parse_metric(sort, metric_enum, default)
        rows = scoring.location_leaderboard(drawing_id, metric=metric)
    else:
        metric_enum, default = LeaderboardMetric, LeaderboardMetric.total_absolute_error
        metric = _parse_metric(sort, metric_enum, default)
        rows = scoring.leaderboard(drawing_id, metric=metric)

    # The board rows carry only run_id; look the Drawing up once per Run so a row can show
    # its Drawing and build the GT CTA — enrichment in the web layer, no service change.
    run_meta = {
        run_id: (d_id, d_name)
        for run_id, d_id, d_name in session.exec(
            select(Run.id, Drawing.id, Drawing.name).join(
                Drawing, Run.drawing_id == Drawing.id
            )
        ).all()
    }

    drawings = session.exec(select(Drawing).order_by(Drawing.created_at.desc())).all()

    out_rows: list[LeaderboardRowOut] = []
    rank = 0
    for row in rows:
        if row.scored:
            rank += 1
        d_id, d_name = run_meta[row.run_id]
        common = dict(
            rank=rank if row.scored else None,
            result_id=row.result_id,
            run_id=row.run_id,
            model=row.model,
            prompt_family=row.prompt_family,
            prompt_version=row.prompt_version,
            drawing_id=d_id,
            drawing_name=d_name,
            scored=row.scored,
        )
        # Each board yields its own row dataclass with disjoint metric fields; set only the
        # Task's own pair/triple so the other stays null (the SPA reads by Task).
        if board_task == Task.location:
            out_rows.append(
                LeaderboardRowOut(
                    **common,
                    precision=row.precision,
                    recall=row.recall,
                    f1=row.f1,
                )
            )
        else:
            out_rows.append(
                LeaderboardRowOut(
                    **common,
                    total_absolute_error=row.total_absolute_error,
                    exact_match_count=row.exact_match_count,
                )
            )

    return LeaderboardResponse(
        task=board_task.value,
        drawing_id=drawing_id,
        sort=metric.value,
        metrics=[m.value for m in metric_enum],
        drawings=[
            LeaderboardDrawing(id=d.id, name=d.name, page_count=len(d.pages))
            for d in drawings
        ],
        label_count=len(OBJECT_LABELS),
        rows=out_rows,
    )


class CountingLabelDetail(BaseModel):
    """One label's counting breakdown (mirrors ``scoring.LabelScore``, from
    ``Score.per_label_json``): the summed prediction, the GT total, and the derived
    absolute error + exact-match flag the drill-down table shows."""

    label: str
    predicted: int
    gt: int
    absolute_error: int
    exact_match: bool


class CountingScoreOut(BaseModel):
    """A counting Result's Score block: the two ranked aggregates plus the per-label rows."""

    total_absolute_error: int
    exact_match_count: int
    per_label: list[CountingLabelDetail]


class LocationLabelDetail(BaseModel):
    """One label's location breakdown (mirrors ``scoring.LabelLocationScore``): the
    IoU@0.5 tally and its derived rates."""

    label: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


class LocationScoreOut(BaseModel):
    """A location Result's Score block: the micro-averaged P/R/F1 headline plus the
    per-label rows (subtitle "IoU@0.5, matched per page then micro-averaged")."""

    precision: float
    recall: float
    f1: float
    per_label: list[LocationLabelDetail]


class PredictionOut(BaseModel):
    """One Page's Prediction in the drill-down: the raw model output and parsed JSON on
    success, or a parse-error failure record. ``has_gt`` flags whether the page carries
    location GT (so the SPA can mark a GT-less page in the compare view); ``box_count`` is
    the number of predicted boxes on the page (location only, 0 otherwise)."""

    page_number: int
    status: str
    raw_content: str | None
    parsed_json: str | None
    parse_error: str | None
    has_gt: bool
    box_count: int


class ResultDetailResponse(BaseModel):
    """``GET /api/results/{id}`` (spec §A.3): the header refs, the correct score shape for
    the Run's Task with per-label detail (``null`` when the Drawing has no GT — unscored,
    distinct from scored-zero), and the per-page Predictions. Exactly one of
    ``counting_score`` / ``location_score`` is set, per the Task."""

    result_id: int
    model: str
    task: str
    prompt_family: str
    prompt_version: int
    run_id: int
    drawing_id: int
    drawing_name: str
    scored: bool
    label_count: int
    counting_score: CountingScoreOut | None = None
    location_score: LocationScoreOut | None = None
    predictions: list[PredictionOut]


def _prediction_out(
    pred: Prediction, task: Task, pages_with_gt: set[int]
) -> PredictionOut:
    """Shape one Prediction for the SPA. The predicted box count is parsed only for a
    successful location Prediction; counting/failed rows report 0."""
    box_count = 0
    if (
        task == Task.location
        and pred.status == PredictionStatus.ok
        and pred.parsed_json
    ):
        box_count = len(LocationResult.model_validate_json(pred.parsed_json).detections)
    return PredictionOut(
        page_number=pred.page_number,
        status=pred.status.value,
        raw_content=pred.raw_content,
        parsed_json=pred.parsed_json,
        parse_error=pred.parse_error,
        has_gt=pred.page_number in pages_with_gt,
        box_count=box_count,
    )


@api_router.get("/results/{result_id}", response_model=ResultDetailResponse)
def result_detail(
    result_id: int, session: Session = Depends(get_session)
) -> ResultDetailResponse:
    """JSON twin of the retired Jinja ``/results/{id}`` drill-down (spec §A.3). Scored on
    the Run's own Task so a location Result uses IoU@0.5 P/R/F1 and never gets clobbered by
    counting scoring; the Score is recomputed against current GT on read (ADR 0004), so a
    GT import after the Run shows up without a re-run. Returns ``scored:false`` with a null
    score block when the Drawing has no GT (unscored, distinct from a zero score)."""
    result = session.get(Result, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no result with id {result_id}")
    run = session.get(Run, result.run_id)
    prompt = session.get(Prompt, run.prompt_id)
    drawing = session.get(Drawing, run.drawing_id)
    scoring = ScoringService(session)

    # Which page numbers carry location GT, so the compare view can flag a page whose
    # predictions have no ground truth to compare against. Empty for counting Results.
    pages_with_gt: set[int] = set()
    if run.task == Task.location:
        score = scoring.score_location_result(result_id)
        gt_page_ids = set(LocationGroundTruthService(session).boxes_by_page(drawing.id))
        pages_with_gt = {
            page.page_number for page in drawing.pages if page.id in gt_page_ids
        }
    else:
        score = scoring.score_result(result_id)

    counting_score: CountingScoreOut | None = None
    location_score: LocationScoreOut | None = None
    if score is not None:
        per_label = json.loads(score.per_label_json)
        if run.task == Task.location:
            location_score = LocationScoreOut(
                precision=score.precision,
                recall=score.recall,
                f1=score.f1,
                per_label=[LocationLabelDetail(**ls) for ls in per_label],
            )
        else:
            counting_score = CountingScoreOut(
                total_absolute_error=score.total_absolute_error,
                exact_match_count=score.exact_match_count,
                per_label=[CountingLabelDetail(**ls) for ls in per_label],
            )

    return ResultDetailResponse(
        result_id=result.id,
        model=result.model,
        task=run.task.value,
        prompt_family=prompt.family,
        prompt_version=prompt.version,
        run_id=run.id,
        drawing_id=drawing.id,
        drawing_name=drawing.name,
        scored=score is not None,
        label_count=len(OBJECT_LABELS),
        counting_score=counting_score,
        location_score=location_score,
        predictions=[
            _prediction_out(pred, run.task, pages_with_gt)
            for pred in result.predictions
        ],
    )


# The two binary overlay PNGs re-mounted under /api for the SPA (spec §A.3). The Jinja
# originals in web.app stay live for the HTMX run-status fragment; the handlers here call
# the same stored/rendered PNGs. The prefix keeps these off the client-side routes so the
# SPA's own /results/:id path still falls through to index.html in dev.
@api_router.get("/results/{result_id}/pages/{page_number}/overlay")
def result_overlay(
    result_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> Response:
    """The location prediction-overlay PNG for one (Result, Page): the model's boxes drawn
    on the page image during the Run, served from the cached file (ticket 09)."""
    prediction = session.exec(
        select(Prediction).where(
            Prediction.result_id == result_id,
            Prediction.page_number == page_number,
        )
    ).first()
    if (
        prediction is None
        or not prediction.overlay_path
        or not Path(prediction.overlay_path).exists()
    ):
        raise HTTPException(status_code=404, detail="Overlay not found")
    return FileResponse(prediction.overlay_path, media_type="image/png")


@api_router.get("/results/{result_id}/pages/{page_number}/compare-overlay")
def result_compare_overlay(
    result_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> Response:
    """The GT-vs-prediction compare PNG for one (Result, Page): the model's boxes (red)
    and the page's ground-truth boxes (green) drawn together. Rendered on demand from
    current GT — not the cached prediction overlay — so a GT import after the Run shows up
    without a re-run (ADR 0004). A page with no GT still renders its predictions."""
    prediction = session.exec(
        select(Prediction).where(
            Prediction.result_id == result_id,
            Prediction.page_number == page_number,
        )
    ).first()
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    page = session.get(Page, prediction.page_id)
    if page is None or not Path(page.image_path).exists():
        raise HTTPException(status_code=404, detail="Page image not found")
    detections = (
        LocationResult.model_validate_json(prediction.parsed_json).detections
        if prediction.status == PredictionStatus.ok and prediction.parsed_json
        else []
    )
    gt_boxes = [
        LabeledBox(row.label, row.x_min, row.y_min, row.x_max, row.y_max)
        for row in session.exec(
            select(LocationGroundTruth).where(
                LocationGroundTruth.page_id == prediction.page_id
            )
        ).all()
    ]
    png = render_compare_overlay(Path(page.image_path), detections, gt_boxes)
    return Response(content=png, media_type="image/png")


# --- Runs (spec §A.4) ------------------------------------------------------------------
# The JSON twins of the Jinja ``/runs`` launch page and its HTMX status poll. The web
# layer changes only: launch still resolves the slug list server-side
# (``ModelCatalogService.resolve_selection``) as the source of truth, and execution still
# fans out on the in-process background runner (ADR 0006). The SPA replaces HTMX polling
# with React-side polling that stops at a terminal state.


def get_run_service(
    session: Session = Depends(get_session),
    adapter: OpenRouterAdapter = Depends(get_openrouter_adapter),
) -> RunService:
    """A ``RunService`` bound to the request session; the adapter is overridable in tests.
    Defined here (not imported from ``web.app``) so this router carries no import cycle
    back to the app factory."""
    return RunService(session, adapter)


class RunHistoryItem(BaseModel):
    """One row of the run history list: the launch tuple plus live progress, denormalized
    with the prompt family/version and drawing name so the list renders without follow-up
    fetches (``Run #id — task — status (progress n / total)``)."""

    id: int
    task: str
    status: str
    progress: int
    total_units: int
    prompt_family: str
    prompt_version: int
    drawing_name: str
    created_at: datetime


class RunHistoryResponse(BaseModel):
    runs: list[RunHistoryItem]


class LaunchPrompt(BaseModel):
    """One selectable prompt version; its ``task`` drives the launched Run's Task."""

    id: int
    task: str
    family: str
    version: int


class CatalogEntryOut(BaseModel):
    """One curated model the launch form renders as a checkbox/chip."""

    slug: str
    label: str


class LaunchOptionsResponse(BaseModel):
    """Everything the launch form needs (was baked into ``runs.html``): the prompt
    versions, the drawings, and the curated model catalog. An empty ``prompts`` or
    ``drawings`` tells the SPA to guide the developer to create one, not show a dead
    form."""

    prompts: list[LaunchPrompt]
    drawings: list[LeaderboardDrawing]
    catalog: list[CatalogEntryOut]


class RunCreateRequest(BaseModel):
    """The launch body. The server resolves ``models`` (curated) + ``free_text`` into the
    final slug list as the source of truth, and runs against the prompt's own Task."""

    prompt_id: int
    drawing_id: int
    models: list[str] = []
    free_text: str = ""


class RunCreatedOut(BaseModel):
    """The just-queued Run returned from ``POST /api/runs`` so the SPA routes to it."""

    id: int
    status: str
    task: str
    total_units: int


class RunResultOut(BaseModel):
    """One model's Result row under a Run; each links to its Result drill-down once the
    Run is terminal."""

    id: int
    model: str


class RunRef(BaseModel):
    """The run header + live progress the detail page renders."""

    id: int
    task: str
    status: str
    progress: int
    total_units: int


class PromptRef(BaseModel):
    family: str
    version: int


class DrawingRef(BaseModel):
    id: int
    name: str


class KnobsOut(BaseModel):
    """The read-only fixed-knobs snapshot a Run recorded (spec: Runs 18)."""

    dpi: int
    downsample_px: int
    max_tokens: int
    prefill: bool
    temperature: float


class RunDetailResponse(BaseModel):
    """``GET /api/runs/{id}``: the header, the fixed-knobs snapshot as read-only metadata,
    and the result rows (present from launch; each becomes viewable once terminal)."""

    run: RunRef
    prompt: PromptRef
    drawing: DrawingRef
    knobs: KnobsOut
    results: list[RunResultOut]


class RunStatusResponse(BaseModel):
    """``GET /api/runs/{id}/status``: the small, cheap poll target. The SPA polls it while
    non-terminal and stops at ``done``/``failed`` (the JSON analog of the HTMX template
    dropping its ``hx-trigger``), then swaps in the results."""

    status: str
    progress: int
    total_units: int
    results: list[RunResultOut]


@api_router.get("/runs", response_model=RunHistoryResponse)
def list_runs(session: Session = Depends(get_session)) -> RunHistoryResponse:
    """Run history, newest-first. Joined to Prompt + Drawing so each row shows its prompt
    family/version and drawing name without an N+1 follow-up."""
    rows = session.exec(
        select(Run, Prompt.family, Prompt.version, Drawing.name)
        .join(Prompt, Run.prompt_id == Prompt.id)
        .join(Drawing, Run.drawing_id == Drawing.id)
        .order_by(Run.created_at.desc())
    ).all()
    return RunHistoryResponse(
        runs=[
            RunHistoryItem(
                id=run.id,
                task=run.task.value,
                status=run.status.value,
                progress=run.progress,
                total_units=run.total_units,
                prompt_family=family,
                prompt_version=version,
                drawing_name=name,
                created_at=run.created_at,
            )
            for run, family, version, name in rows
        ]
    )


@api_router.get("/runs/launch-options", response_model=LaunchOptionsResponse)
def launch_options(session: Session = Depends(get_session)) -> LaunchOptionsResponse:
    """The launch form's option set. Every prompt version is offered; the chosen prompt's
    own Task drives the Run, so there is no separate task picker (mirrors the Jinja page).
    """
    prompts = session.exec(
        select(Prompt).order_by(Prompt.task, Prompt.family, Prompt.version.desc())
    ).all()
    drawings = session.exec(select(Drawing).order_by(Drawing.created_at.desc())).all()
    catalog = ModelCatalogService(session).list_catalog()
    return LaunchOptionsResponse(
        prompts=[
            LaunchPrompt(id=p.id, task=p.task.value, family=p.family, version=p.version)
            for p in prompts
        ],
        drawings=[
            LeaderboardDrawing(id=d.id, name=d.name, page_count=len(d.pages))
            for d in drawings
        ],
        catalog=[CatalogEntryOut(slug=c.slug, label=c.label) for c in catalog],
    )


@api_router.post("/runs", response_model=RunCreatedOut, status_code=201)
def create_run(
    payload: RunCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
    service: RunService = Depends(get_run_service),
) -> RunCreatedOut:
    """Insert the queued Run and return at once; the fan-out runs on an in-process
    background task the SPA then polls (ADR 0006). The Run's Task is the chosen prompt's
    own Task; the slug list is resolved server-side as the source of truth. A bad prompt
    or an empty selection is a ``400`` (mirrors the Jinja launch handler)."""
    prompt = session.get(Prompt, payload.prompt_id)
    if prompt is None:
        raise HTTPException(
            status_code=400, detail=f"no prompt with id {payload.prompt_id}"
        )
    slugs = ModelCatalogService.resolve_selection(payload.models, payload.free_text)
    try:
        run = service.create_run(
            prompt.task, payload.prompt_id, payload.drawing_id, slugs
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    service.background_runner(request.app.state.engine).submit(run.id)
    return RunCreatedOut(
        id=run.id,
        status=run.status.value,
        task=run.task.value,
        total_units=run.total_units,
    )


@api_router.get("/runs/{run_id}", response_model=RunDetailResponse)
def run_detail(
    run_id: int, session: Session = Depends(get_session)
) -> RunDetailResponse:
    """The run detail: header, the read-only fixed-knobs snapshot, and the result rows."""
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run with id {run_id}")
    prompt = session.get(Prompt, run.prompt_id)
    drawing = session.get(Drawing, run.drawing_id)
    return RunDetailResponse(
        run=RunRef(
            id=run.id,
            task=run.task.value,
            status=run.status.value,
            progress=run.progress,
            total_units=run.total_units,
        ),
        prompt=PromptRef(family=prompt.family, version=prompt.version),
        drawing=DrawingRef(id=drawing.id, name=drawing.name),
        knobs=KnobsOut(
            dpi=run.dpi,
            downsample_px=run.downsample_px,
            max_tokens=run.max_tokens,
            prefill=run.prefill,
            temperature=run.temperature,
        ),
        results=[RunResultOut(id=r.id, model=r.model) for r in run.results],
    )


@api_router.get("/runs/{run_id}/status", response_model=RunStatusResponse)
def run_status(
    run_id: int, session: Session = Depends(get_session)
) -> RunStatusResponse:
    """The poll target: live progress while non-terminal, and the result rows so the SPA
    can swap them in once the Run reaches ``done``/``failed`` and stops polling."""
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run with id {run_id}")
    return RunStatusResponse(
        status=run.status.value,
        progress=run.progress,
        total_units=run.total_units,
        results=[RunResultOut(id=r.id, model=r.model) for r in run.results],
    )


# --- Prompts (spec §A.5) ---------------------------------------------------------------
# The JSON twins of the Jinja ``/prompts`` list, its authoring form, the version-history
# page, and its "edit" form. Same ``PromptService`` methods, so authoring appends a new
# immutable version rather than mutating (ADR 0009) and Task-scoping is preserved — a
# counting prompt is never grouped under location. Each version's text rides along in the
# history response so the SPA can compare/read versions without an extra round-trip.


class PromptFamilyOut(BaseModel):
    """One family in the Task-grouped list: its name, its newest version, and how many
    versions it has (so the list shows ``latest vN · N versions`` without a follow-up).
    """

    name: str
    latest_version: int
    count: int


class PromptGroupOut(BaseModel):
    """All of one Task's families (Task-scoping, ADR 0009)."""

    task: str
    families: list[PromptFamilyOut]


class PromptsResponse(BaseModel):
    """``GET /api/prompts``: the fixed Task taxonomy plus each Task's families, so the SPA
    renders a group per Task even when a Task has no families yet."""

    tasks: list[str]
    groups: list[PromptGroupOut]


class PromptVersionOut(BaseModel):
    """One immutable version in a family's history: its number, full text, and authored
    time. The text rides along so the compare/read view needs no extra fetch (spec §A.5).
    """

    version: int
    text: str
    created_at: datetime


class PromptHistoryResponse(BaseModel):
    """``GET /api/prompts/{task}/{family}``: the family's versions newest-first."""

    task: str
    family: str
    versions: list[PromptVersionOut]


class PromptCreateRequest(BaseModel):
    """``POST /api/prompts`` body: author a new family's v1 for a Task."""

    task: Task
    family: str
    text: str


class PromptVersionCreateRequest(BaseModel):
    """``POST /api/prompts/{task}/{family}/versions`` body: the "edit" that appends the
    next immutable version. Only the text changes; the ``(task, family)`` come from the URL.
    """

    text: str


class PromptVersionRef(BaseModel):
    """The just-written version returned from create/append so the SPA routes to it."""

    task: str
    family: str
    version: int


@api_router.get("/prompts", response_model=PromptsResponse)
def list_prompts(session: Session = Depends(get_session)) -> PromptsResponse:
    """Families grouped by Task with each family's latest version + count (spec §A.5).
    Every Task appears even with no families, so the SPA renders an empty group rather
    than dropping the Task."""
    service = PromptService(session)
    groups: list[PromptGroupOut] = []
    for task in Task:
        families: list[PromptFamilyOut] = []
        for family in service.families(task):
            versions = service.history(task, family)
            families.append(
                PromptFamilyOut(
                    name=family,
                    latest_version=versions[0].version,
                    count=len(versions),
                )
            )
        groups.append(PromptGroupOut(task=task.value, families=families))
    return PromptsResponse(tasks=[t.value for t in Task], groups=groups)


@api_router.post("/prompts", response_model=PromptVersionRef, status_code=201)
def create_prompt(
    payload: PromptCreateRequest,
    session: Session = Depends(get_session),
) -> PromptVersionRef:
    """Author a new family at v1 (spec §A.5). A duplicate family is the service's
    ``ValueError`` surfaced as ``400`` with its message (mirrors the Jinja handler)."""
    family = payload.family.strip()
    try:
        prompt = PromptService(session).create(
            payload.task, family=family, text=payload.text
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PromptVersionRef(
        task=prompt.task.value, family=prompt.family, version=prompt.version
    )


@api_router.get("/prompts/{task}/{family}", response_model=PromptHistoryResponse)
def prompt_history(
    task: Task, family: str, session: Session = Depends(get_session)
) -> PromptHistoryResponse:
    """A family's immutable version history newest-first, each version's text included so
    the compare/read view needs no extra round-trip (spec §A.5). An unknown family (no
    versions) is a ``404``."""
    versions = PromptService(session).history(task, family)
    if not versions:
        raise HTTPException(
            status_code=404,
            detail=f"no prompt family {family!r} for task {task.value}",
        )
    return PromptHistoryResponse(
        task=task.value,
        family=family,
        versions=[
            PromptVersionOut(version=p.version, text=p.text, created_at=p.created_at)
            for p in versions
        ],
    )


@api_router.post(
    "/prompts/{task}/{family}/versions",
    response_model=PromptVersionRef,
    status_code=201,
)
def append_prompt_version(
    task: Task,
    family: str,
    payload: PromptVersionCreateRequest,
    session: Session = Depends(get_session),
) -> PromptVersionRef:
    """ "Edit" = append the next immutable version (ADR 0009); prior versions are never
    mutated (spec §A.5). An unknown family is the service's ``ValueError`` surfaced as a
    ``404`` (mirrors the Jinja edit handler)."""
    try:
        prompt = PromptService(session).edit(task, family=family, text=payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PromptVersionRef(
        task=prompt.task.value, family=prompt.family, version=prompt.version
    )


# --- Library (spec §A.6) ---------------------------------------------------------------
# The JSON twins of the Jinja ``/drawings`` list/upload/detail pages, the cached page-image
# PNG re-mounted under ``/api`` for the SPA, and the ``/models`` curated catalog. Ingestion
# reuses ``DrawingService`` unchanged — only the web layer differs. Ground-truth entry
# (counting form, COCO import) is ticket 07; the Drawing detail is only the entry point it
# hangs off.


def get_drawing_service(session: Session = Depends(get_session)) -> DrawingService:
    """The ingestion service for the upload route; override in tests to inject a fast,
    low-DPI service. Defined here (not imported from ``web.app``) so this router carries no
    import cycle back to the app factory — the same pattern as ``get_run_service``."""
    return DrawingService(session)


class DrawingSummary(BaseModel):
    """One Drawing in the Library list / created-upload response: its id, name, and page
    count (the same shape the launch form's Drawing dropdown uses)."""

    id: int
    name: str
    page_count: int


class DrawingsResponse(BaseModel):
    drawings: list[DrawingSummary]


class DrawingPageOut(BaseModel):
    """One rendered Page on the Drawing detail: its number, the full-resolution pixel dims
    (COCO boxes are annotated against these), and the URL of its cached image PNG."""

    page_number: int
    width_px: int
    height_px: int
    image_url: str


class DrawingDetailResponse(BaseModel):
    """``GET /api/drawings/{id}``: the Drawing plus its rendered Pages. The detail is where
    ground-truth entry hangs off (ticket 07)."""

    drawing: DrawingRef
    pages: list[DrawingPageOut]


class ModelsResponse(BaseModel):
    """``GET /api/models``: the curated catalog. Model *selection* still happens inline at
    Run launch; this is catalog *viewing* only (ADR 0011)."""

    catalog: list[CatalogEntryOut]


@api_router.get("/drawings", response_model=DrawingsResponse)
def list_drawings(session: Session = Depends(get_session)) -> DrawingsResponse:
    """Every Drawing with its page count, newest-first (spec §A.6)."""
    drawings = session.exec(select(Drawing).order_by(Drawing.created_at.desc())).all()
    return DrawingsResponse(
        drawings=[
            DrawingSummary(id=d.id, name=d.name, page_count=len(d.pages))
            for d in drawings
        ]
    )


@api_router.post("/drawings", response_model=DrawingSummary, status_code=201)
async def upload_drawing(
    file: UploadFile,
    service: DrawingService = Depends(get_drawing_service),
) -> DrawingSummary:
    """Ingest an uploaded PDF (multipart) via ``DrawingService`` and return the created
    Drawing (spec §A.6). Upload stays ``multipart/form-data``; the file is written to a
    temp PDF the service renders, then removed."""
    name = Path(file.filename or "drawing").stem or "drawing"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        drawing = service.ingest(tmp_path, name=name)
    finally:
        tmp_path.unlink(missing_ok=True)
    return DrawingSummary(
        id=drawing.id, name=drawing.name, page_count=len(drawing.pages)
    )


@api_router.get("/drawings/{drawing_id}", response_model=DrawingDetailResponse)
def drawing_detail(
    drawing_id: int, session: Session = Depends(get_session)
) -> DrawingDetailResponse:
    """The Drawing's rendered Pages with pixel dims + image URLs (spec §A.6). The page
    image URLs point at the ``/api`` PNG route so the SPA fetches under one origin. An
    unknown Drawing is a ``404``."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return DrawingDetailResponse(
        drawing=DrawingRef(id=drawing.id, name=drawing.name),
        pages=[
            DrawingPageOut(
                page_number=page.page_number,
                width_px=page.width_px,
                height_px=page.height_px,
                image_url=f"/api/drawings/{drawing_id}/pages/{page.page_number}/image",
            )
            for page in drawing.pages
        ],
    )


@api_router.get("/drawings/{drawing_id}/pages/{page_number}/image")
def drawing_page_image(
    drawing_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> Response:
    """The cached, downsampled page image PNG for one (Drawing, Page), re-mounted under
    ``/api`` for the SPA (the Jinja twin stays live for HTMX). Unchanged handler."""
    page = session.exec(
        select(Page).where(
            Page.drawing_id == drawing_id, Page.page_number == page_number
        )
    ).first()
    if page is None or not Path(page.image_path).exists():
        raise HTTPException(status_code=404, detail="Page image not found")
    return FileResponse(page.image_path, media_type="image/png")


# --- Ground-truth entry (ticket 07) ---------------------------------------------------
# The scoring payoff: recording ground truth for a Drawing turns its previously-unscored
# Leaderboard/Result rows into scored ones with **no re-run** (scores recompute on read in
# the service layer). Two entry points hang off the Drawing detail — a counting number-per-
# label form and a COCO location import — both reusing the existing services unchanged; only
# the web layer differs. The counting endpoints are the JSON twins of the retired Jinja
# form; the location import surfaces ticket-10's importer (previously CLI-only, spec §A.6).


class CountingGtLabel(BaseModel):
    """One taxonomy label's stored total, or ``null`` when it has not been entered yet — the
    null lets the form tell "unentered" from "entered zero" (matches the service's
    ``get_totals`` semantics)."""

    name: str
    value: int | None


class CountingGroundTruthResponse(BaseModel):
    """``GET``/``PUT /api/drawings/{id}/counting-ground-truth``: the per-label totals in the
    fixed taxonomy order. The same shape is returned for pre-fill and after a save (the
    just-saved totals), so the SPA can seed the form and update its cache from one shape.
    """

    drawing_id: int
    labels: list[CountingGtLabel]


class ImportProblemOut(BaseModel):
    """One annotation the COCO import reported rather than silently dropped: an unmapped
    label (``unmapped_label``) or a reference to a page the Drawing lacks (``unknown_page``).
    """

    kind: str
    detail: str


class LocationImportResponse(BaseModel):
    """``POST /api/drawings/{id}/location-ground-truth``: the ``CocoImportResult`` — how many
    boxes were created and every problem reported (spec §A.6)."""

    created: int
    problems: list[ImportProblemOut]


def _counting_gt_response(
    drawing_id: int, totals: dict[str, int]
) -> CountingGroundTruthResponse:
    """Build the pre-fill/saved response from a label→total map, filling every taxonomy
    label in order and leaving an unentered label ``null``."""
    return CountingGroundTruthResponse(
        drawing_id=drawing_id,
        labels=[
            CountingGtLabel(name=label, value=totals.get(label))
            for label in OBJECT_LABELS
        ],
    )


@api_router.get(
    "/drawings/{drawing_id}/counting-ground-truth",
    response_model=CountingGroundTruthResponse,
)
def counting_ground_truth(
    drawing_id: int, session: Session = Depends(get_session)
) -> CountingGroundTruthResponse:
    """Pre-fill the counting form with each taxonomy label's stored total (spec §A.6). An
    unentered label is ``null`` so the form distinguishes it from an entered zero. Unknown
    Drawing → ``404``."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    totals = CountingGroundTruthService(session).get_totals(drawing_id)
    return _counting_gt_response(drawing_id, totals)


@api_router.put(
    "/drawings/{drawing_id}/counting-ground-truth",
    response_model=CountingGroundTruthResponse,
)
def save_counting_ground_truth(
    drawing_id: int,
    payload: dict = Body(...),
    session: Session = Depends(get_session),
) -> CountingGroundTruthResponse:
    """Upsert the per-label counting totals (spec §A.6). Every taxonomy label is required —
    a missing or non-integer total is a ``400`` (the same rule the retired Jinja form
    enforced), validated in-route to keep the ``{"detail": …}`` envelope (a Pydantic model
    would raise ``422``). Returns the saved totals. Unknown Drawing → ``404``."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    try:
        totals = {label: payload[label] for label in OBJECT_LABELS}
    except KeyError:
        raise HTTPException(
            status_code=400, detail="A total is required for every label"
        )
    # Require a real integer per label — reject strings and floats (a truncated 2.7 would
    # be a silent wrong answer) rather than coercing. ``bool`` is an ``int`` subclass, so
    # exclude it explicitly.
    if any(isinstance(v, bool) or not isinstance(v, int) for v in totals.values()):
        raise HTTPException(status_code=400, detail="Every total must be an integer")
    CountingGroundTruthService(session).save(drawing_id, totals)
    return _counting_gt_response(drawing_id, totals)


@api_router.post(
    "/drawings/{drawing_id}/location-ground-truth",
    response_model=LocationImportResponse,
)
async def import_location_ground_truth(
    drawing_id: int,
    file: UploadFile,
    label_map: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> LocationImportResponse:
    """Import a COCO JSON upload as this Drawing's LocationGroundTruth via the existing
    importer (spec §A.6), surfacing its problem report (unmapped labels / unknown pages)
    instead of silently dropping them. The optional ``label_map`` is a JSON object mapping
    external category names onto the taxonomy. A non-JSON file, a non-object COCO body, a
    bad ``label_map``, or a map targeting a label outside the taxonomy is a ``400``; an
    unknown Drawing is a ``404``. Re-importing replaces the Drawing's existing boxes."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")

    try:
        coco = json.loads(await file.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Uploaded file is not valid JSON")
    if not isinstance(coco, dict):
        raise HTTPException(status_code=400, detail="COCO JSON must be an object")

    parsed_map: dict | None = None
    if label_map:
        try:
            parsed_map = json.loads(label_map)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="label_map is not valid JSON")
        if not isinstance(parsed_map, dict):
            raise HTTPException(
                status_code=400, detail="label_map must be a JSON object"
            )

    try:
        result = LocationGroundTruthService(session).import_coco(
            drawing_id, coco, label_map=parsed_map
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return LocationImportResponse(
        created=result.created,
        problems=[
            ImportProblemOut(kind=problem.kind, detail=problem.detail)
            for problem in result.problems
        ],
    )


@api_router.get("/models", response_model=ModelsResponse)
def list_models(session: Session = Depends(get_session)) -> ModelsResponse:
    """The curated model catalog (spec §A.6). The free-text escape hatch is a client-side
    input resolved at Run launch, not part of this list."""
    catalog = ModelCatalogService(session).list_catalog()
    return ModelsResponse(
        catalog=[CatalogEntryOut(slug=e.slug, label=e.label) for e in catalog]
    )
