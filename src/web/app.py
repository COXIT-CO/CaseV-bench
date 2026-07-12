"""FastAPI app serving Jinja2 templates with HTMX (ADR 0005).

``create_app()`` is a factory so tests can build the app against a temp SQLite engine
and override the OpenRouter adapter. The schema is created on startup (ADR 0008).
"""

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlmodel import Session, select

from adapters.openrouter import OpenRouterAdapter, get_openrouter_adapter
from db import get_session, init_db, make_engine
from models.drawing import Drawing, Page
from models.prompt import Prompt, Task
from models.run import Run
from services.drawing import DrawingService
from services.model_catalog import ModelCatalogService
from services.prompt import PromptService, seed_default_prompts
from services.run import RunService

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
        prompt_id: int = Form(...),
        drawing_id: int = Form(...),
        models: list[str] = Form(default=[]),
        free_text: str = Form(default=""),
        service: RunService = Depends(get_run_service),
    ) -> HTMLResponse:
        slugs = ModelCatalogService.resolve_selection(models, free_text)
        try:
            run = service.launch(Task.counting, prompt_id, drawing_id, slugs)
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=400)
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

    return app


app = create_app()
