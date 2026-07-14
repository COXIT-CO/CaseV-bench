"""FastAPI app serving Jinja2 templates with HTMX (ADR 0005).

``create_app()`` is a factory so tests can build the app against a temp SQLite engine
and override the OpenRouter adapter. The schema is created on startup (ADR 0008).
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlmodel import Session, select

from db import get_session, init_db, make_engine
from models.drawing import Drawing, Page
from models.results import OBJECT_LABELS
from services.counting_ground_truth import CountingGroundTruthService
from services.model_catalog import ModelCatalogService
from services.prompt import seed_default_prompts
from web.api import api_router

APP_TITLE = "Prompt & Config Lab"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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

    # The JSON API (ADR 0010) is mounted additively under /api; the Jinja/HTMX routes
    # below are left unchanged so the live app keeps serving until each slice reaches
    # parity. Registered first so /api/** never falls through to an HTML handler.
    app.include_router(api_router)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"title": APP_TITLE})

    # The ``/drawings`` list, upload, and detail Jinja pages were retired here — the Library
    # catalog now lives in the React SPA against ``GET /api/drawings`` (list), ``POST
    # /api/drawings`` (upload), and ``GET /api/drawings/{id}`` (detail), with the page image
    # re-mounted at ``GET /api/drawings/{id}/pages/{n}/image`` (spec §A.6). The cached
    # page-image route below is kept as HTMX's copy per ticket 06 ("the original remains for
    # HTMX"); with drawing_detail retired it has no remaining HTMX consumer, so ticket 07 can
    # drop it when it migrates the last ground-truth entry. ``counting_ground_truth`` stays
    # until then.
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

    # The Jinja ``/leaderboard`` (ticket 02), ``/results/{id}`` (ticket 03), ``/runs``
    # launch/detail/status pages (ticket 04), ``/prompts`` list/authoring/history pages
    # (ticket 05), and the ``/drawings`` list/upload/detail + ``/models`` catalog pages
    # (ticket 06) were retired here: all now live in the React SPA against
    # ``GET /api/leaderboard`` (§A.2), ``GET /api/results/{id}`` (§A.3), the ``/api/runs``
    # family (§A.4), the ``/api/prompts`` family (§A.5), and the Library ``/api/drawings`` +
    # ``/api/models`` endpoints (§A.6). Their binary PNG routes moved with them to ``/api``
    # (prediction/compare overlays and the page image), so the SPA fetches every asset under
    # one ``/api`` prefix. Only the HTMX counting-ground-truth form (and the page-image route
    # it reuses) remains, until ticket 07 migrates ground-truth entry.

    return app


app = create_app()
