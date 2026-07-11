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

from db import init_db, make_engine

APP_TITLE = "Prompt & Config Lab"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.engine)
        yield

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.state.engine = engine or make_engine()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"title": APP_TITLE})

    return app


app = create_app()
