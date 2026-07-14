"""Runs (spec §A.4): run history, the launch form's options, launch, and the detail +
status poll. Launch resolves the slug list server-side as the source of truth and fans out
on the in-process background runner (ADR 0006); the SPA polls status and stops when terminal.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from api.deps import get_run_service, get_session
from api.routers.common import CatalogEntryOut, DrawingRef, LeaderboardDrawing
from core.models.drawing import Drawing
from core.models.prompt import Prompt
from core.models.run import Run
from core.services.model_catalog import ModelCatalogService
from core.services.run import RunService

router = APIRouter(prefix="/api", tags=["runs"])


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


@router.get("/runs", response_model=RunHistoryResponse)
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


@router.get("/runs/launch-options", response_model=LaunchOptionsResponse)
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


@router.post("/runs", response_model=RunCreatedOut, status_code=201)
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


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
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


@router.get("/runs/{run_id}/status", response_model=RunStatusResponse)
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
