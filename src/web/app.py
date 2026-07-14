"""FastAPI app serving Jinja2 templates with HTMX (ADR 0005).

``create_app()`` is a factory so tests can build the app against a temp SQLite engine
and override the OpenRouter adapter. The schema is created on startup (ADR 0008).
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlmodel import Session

from db import init_db, make_engine
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

    # The JSON API (ADR 0010) is mounted additively under /api; the Jinja index below is
    # left unchanged so the live app keeps serving until the final cutover (ticket 08).
    # Registered first so /api/** never falls through to an HTML handler.
    app.include_router(api_router)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"title": APP_TITLE})

    # Every feature page has now migrated to the React SPA against ``/api``: the Leaderboard
    # (ticket 02), Result detail (03), Runs (04), Prompts (05), the Drawings + Models Library
    # (06), and ground-truth entry — the counting form and the new COCO location import — in
    # this slice (07). The retired counting-ground-truth Jinja form and the HTMX page-image
    # route it shared are gone; ground-truth entry now lives at ``GET``/``PUT
    # /api/drawings/{id}/counting-ground-truth`` and ``POST
    # /api/drawings/{id}/location-ground-truth`` (spec §A.6). Only the placeholder ``/`` index
    # remains on the HTMX surface, retired in the ticket-08 cutover.

    return app


app = create_app()
