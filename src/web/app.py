"""FastAPI app serving Jinja2 templates with HTMX (ADR 0005).

``create_app()`` is a factory so tests can build the app against a temp SQLite engine
and override the OpenRouter adapter. The schema is created on startup (ADR 0008).
"""

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlmodel import Session, select

from adapters.openrouter import OpenRouterAdapter, get_openrouter_adapter
from db import get_session, init_db, make_engine
from models.drawing import Drawing, Page
from models.prompt import Prompt, Task
from models.results import OBJECT_LABELS
from models.run import Result, Run
from services.counting_ground_truth import CountingGroundTruthService
from services.drawing import DrawingService
from services.model_catalog import ModelCatalogService
from services.prompt import PromptService, seed_default_prompts
from services.run import RunService
from services.scoring import LeaderboardMetric, ScoringService

APP_TITLE = "Prompt & Config Lab"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_drawing_service(session: Session = Depends(get_session)) -> DrawingService:
    """FastAPI dependency; override in tests to inject a fast/low-DPI service."""
    return DrawingService(session)


def get_prompt_service(session: Session = Depends(get_session)) -> PromptService:
    """FastAPI dependency yielding a PromptService bound to the request session."""
    return PromptService(session)


def get_run_service(
    session: Session = Depends(get_session),
    adapter: OpenRouterAdapter = Depends(get_openrouter_adapter),
) -> RunService:
    """FastAPI dependency yielding a RunService; the adapter is overridable in tests."""
    return RunService(session, adapter)


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.engine)
        with Session(app.state.engine) as session:
            ModelCatalogService(session).seed_defaults()
            seed_default_prompts(session)
        yield

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.state.engine = engine or make_engine()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"title": APP_TITLE})

    @app.get("/drawings", response_class=HTMLResponse)
    def list_drawings(
        request: Request, session: Session = Depends(get_session)
    ) -> HTMLResponse:
        drawings = session.exec(
            select(Drawing).order_by(Drawing.created_at.desc())
        ).all()
        rows = [{"drawing": d, "page_count": len(d.pages)} for d in drawings]
        return templates.TemplateResponse(
            request, "drawings.html", {"title": APP_TITLE, "rows": rows}
        )

    @app.post("/drawings")
    async def upload_drawing(
        file: UploadFile,
        service: DrawingService = Depends(get_drawing_service),
    ) -> RedirectResponse:
        name = Path(file.filename or "drawing").stem or "drawing"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)
        try:
            service.ingest(tmp_path, name=name)
        finally:
            tmp_path.unlink(missing_ok=True)
        return RedirectResponse(url="/drawings", status_code=303)

    @app.get("/drawings/{drawing_id}", response_class=HTMLResponse)
    def view_drawing(
        drawing_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        drawing = session.get(Drawing, drawing_id)
        if drawing is None:
            return HTMLResponse("Drawing not found", status_code=404)
        pages = [
            {
                "page_number": page.page_number,
                "width_px": page.width_px,
                "height_px": page.height_px,
                "image_url": f"/drawings/{drawing_id}/pages/{page.page_number}/image",
            }
            for page in drawing.pages
        ]
        return templates.TemplateResponse(
            request,
            "drawing_detail.html",
            {"title": APP_TITLE, "drawing": drawing, "pages": pages},
        )

    @app.get("/drawings/{drawing_id}/pages/{page_number}/image")
    def page_image(
        drawing_id: int,
        page_number: int,
        session: Session = Depends(get_session),
    ) -> FileResponse:
        page = session.exec(
            select(Page).where(
                Page.drawing_id == drawing_id, Page.page_number == page_number
            )
        ).first()
        if page is None or not Path(page.image_path).exists():
            return HTMLResponse("Page image not found", status_code=404)
        return FileResponse(page.image_path, media_type="image/png")

    @app.get(
        "/drawings/{drawing_id}/counting-ground-truth", response_class=HTMLResponse
    )
    def counting_ground_truth_form(
        drawing_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        drawing = session.get(Drawing, drawing_id)
        if drawing is None:
            return HTMLResponse("Drawing not found", status_code=404)
        # Pre-fill each taxonomy label with its stored total for editing (ticket 07).
        totals = CountingGroundTruthService(session).get_totals(drawing_id)
        labels = [
            {"name": label, "value": totals.get(label)} for label in OBJECT_LABELS
        ]
        return templates.TemplateResponse(
            request,
            "counting_ground_truth.html",
            {"title": APP_TITLE, "drawing": drawing, "labels": labels},
        )

    @app.post("/drawings/{drawing_id}/counting-ground-truth")
    async def save_counting_ground_truth(
        drawing_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ) -> Response:
        drawing = session.get(Drawing, drawing_id)
        if drawing is None:
            return HTMLResponse("Drawing not found", status_code=404)
        form = await request.form()
        try:
            totals = {label: int(form[label]) for label in OBJECT_LABELS}
        except (KeyError, ValueError):
            return HTMLResponse("A total is required for every label", status_code=400)
        CountingGroundTruthService(session).save(drawing_id, totals)
        return RedirectResponse(
            url=f"/drawings/{drawing_id}/counting-ground-truth", status_code=303
        )

    @app.get("/models", response_class=HTMLResponse)
    def select_models(
        request: Request, session: Session = Depends(get_session)
    ) -> HTMLResponse:
        catalog = ModelCatalogService(session).list_catalog()
        return templates.TemplateResponse(
            request,
            "models.html",
            {"title": APP_TITLE, "catalog": catalog, "selected": None},
        )

    @app.post("/models", response_class=HTMLResponse)
    def resolve_models(
        request: Request,
        models: list[str] = Form(default=[]),
        free_text: str = Form(default=""),
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        service = ModelCatalogService(session)
        selected = service.resolve_selection(models, free_text)
        return templates.TemplateResponse(
            request,
            "models.html",
            {
                "title": APP_TITLE,
                "catalog": service.list_catalog(),
                "selected": selected,
            },
        )

    @app.get("/prompts", response_class=HTMLResponse)
    def list_prompts(
        request: Request, service: PromptService = Depends(get_prompt_service)
    ) -> HTMLResponse:
        # Group families under their Task so a counting prompt is never shown as a
        # location option (Task-scoping, ADR 0009).
        groups = []
        for task in Task:
            families = []
            for family in service.families(task):
                versions = service.history(task, family)
                families.append(
                    {
                        "name": family,
                        "latest_version": versions[0].version,
                        "count": len(versions),
                    }
                )
            groups.append({"task": task.value, "families": families})
        return templates.TemplateResponse(
            request,
            "prompts.html",
            {"title": APP_TITLE, "groups": groups, "tasks": [t.value for t in Task]},
        )

    @app.post("/prompts")
    def author_prompt(
        task: Task = Form(...),
        family: str = Form(...),
        text: str = Form(...),
        service: PromptService = Depends(get_prompt_service),
    ) -> HTMLResponse:
        family = family.strip()
        try:
            service.create(task, family=family, text=text)
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=400)
        return RedirectResponse(url=f"/prompts/{task.value}/{family}", status_code=303)

    @app.get("/prompts/{task}/{family}", response_class=HTMLResponse)
    def view_prompt_family(
        task: Task,
        family: str,
        request: Request,
        service: PromptService = Depends(get_prompt_service),
    ) -> HTMLResponse:
        versions = service.history(task, family)
        if not versions:
            return HTMLResponse("Prompt family not found", status_code=404)
        return templates.TemplateResponse(
            request,
            "prompt_history.html",
            {
                "title": APP_TITLE,
                "task": task.value,
                "family": family,
                "versions": versions,
            },
        )

    @app.post("/prompts/{task}/{family}")
    def edit_prompt_family(
        task: Task,
        family: str,
        text: str = Form(...),
        service: PromptService = Depends(get_prompt_service),
    ) -> HTMLResponse:
        try:
            service.edit(task, family=family, text=text)
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=404)
        return RedirectResponse(url=f"/prompts/{task.value}/{family}", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    def list_runs(
        request: Request, session: Session = Depends(get_session)
    ) -> HTMLResponse:
        # Counting-only launch (ticket 05): offer counting prompt versions and drawings.
        prompts = session.exec(
            select(Prompt)
            .where(Prompt.task == Task.counting)
            .order_by(Prompt.family, Prompt.version.desc())
        ).all()
        drawings = session.exec(
            select(Drawing).order_by(Drawing.created_at.desc())
        ).all()
        catalog = ModelCatalogService(session).list_catalog()
        runs = session.exec(select(Run).order_by(Run.created_at.desc())).all()
        return templates.TemplateResponse(
            request,
            "runs.html",
            {
                "title": APP_TITLE,
                "prompts": prompts,
                "drawings": drawings,
                "catalog": catalog,
                "runs": runs,
            },
        )

    @app.post("/runs")
    def launch_run(
        request: Request,
        prompt_id: int = Form(...),
        drawing_id: int = Form(...),
        models: list[str] = Form(default=[]),
        free_text: str = Form(default=""),
        service: RunService = Depends(get_run_service),
    ) -> HTMLResponse:
        # Insert the queued Run and return at once; the fan-out runs on an in-process
        # background task the detail page then polls (ADR 0006).
        slugs = ModelCatalogService.resolve_selection(models, free_text)
        try:
            run = service.create_run(Task.counting, prompt_id, drawing_id, slugs)
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=400)
        service.background_runner(request.app.state.engine).submit(run.id)
        return RedirectResponse(url=f"/runs/{run.id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def view_run(
        run_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        run = session.get(Run, run_id)
        if run is None:
            return HTMLResponse("Run not found", status_code=404)
        prompt = session.get(Prompt, run.prompt_id)
        drawing = session.get(Drawing, run.drawing_id)
        return templates.TemplateResponse(
            request,
            "run_detail.html",
            {
                "title": APP_TITLE,
                "run": run,
                "prompt": prompt,
                "drawing": drawing,
            },
        )

    @app.get("/runs/{run_id}/status", response_class=HTMLResponse)
    def run_status(
        run_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        # The HTMX polling target: renders live progress while running and swaps in the
        # Results once the Run reaches a terminal state, stopping the poll (ADR 0006).
        run = session.get(Run, run_id)
        if run is None:
            return HTMLResponse("Run not found", status_code=404)
        return templates.TemplateResponse(
            request, "run_status.html", {"title": APP_TITLE, "run": run}
        )

    @app.get("/leaderboard", response_class=HTMLResponse)
    def leaderboard(
        request: Request,
        drawing_id: int | None = None,
        sort: str = LeaderboardMetric.total_absolute_error.value,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        # Counting-only for now (ticket 08): filter by Drawing, rank by the chosen metric.
        drawings = session.exec(
            select(Drawing).order_by(Drawing.created_at.desc())
        ).all()
        try:
            metric = LeaderboardMetric(sort)
        except ValueError:
            metric = LeaderboardMetric.total_absolute_error
        rows = ScoringService(session).leaderboard(drawing_id, metric=metric)
        return templates.TemplateResponse(
            request,
            "leaderboard.html",
            {
                "title": APP_TITLE,
                "drawings": drawings,
                "drawing_id": drawing_id,
                "rows": rows,
                "sort": metric.value,
                "metrics": [m.value for m in LeaderboardMetric],
                "label_count": len(OBJECT_LABELS),
            },
        )

    @app.get("/results/{result_id}", response_class=HTMLResponse)
    def view_result(
        result_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        # Drill-down: the Result's Score summary plus its per-page Predictions + raw JSON.
        result = session.get(Result, result_id)
        if result is None:
            return HTMLResponse("Result not found", status_code=404)
        run = session.get(Run, result.run_id)
        prompt = session.get(Prompt, run.prompt_id)
        drawing = session.get(Drawing, run.drawing_id)
        score = ScoringService(session).score_result(result_id)
        per_label = json.loads(score.per_label_json) if score else None
        return templates.TemplateResponse(
            request,
            "result_detail.html",
            {
                "title": APP_TITLE,
                "result": result,
                "run": run,
                "prompt": prompt,
                "drawing": drawing,
                "score": score,
                "per_label": per_label,
                "label_count": len(OBJECT_LABELS),
            },
        )

    return app


app = create_app()
