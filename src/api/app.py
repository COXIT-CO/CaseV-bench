"""FastAPI app: the JSON API (ADR 0010) plus the built React SPA it serves at ``/``.

``create_app()`` is a factory so tests can build the app against a temp SQLite engine,
override the OpenRouter adapter, and point the SPA mount at a fixture build. The schema is
created on startup (ADR 0008).

The web surface is the JSON API — split into one router per resource under ``routers/``
(ADR-0015) — plus its binary-asset routes under ``/api`` and the SPA build. In dev the SPA
runs on the Vite dev server (port 5173) which proxies ``/api`` here; in prod ``vite build``
writes ``src/app/dist`` and FastAPI serves it.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlmodel import Session

from api.routers import all_routers
from core.db import init_db, make_engine
from core.services.model_catalog import ModelCatalogService
from core.services.prompt import seed_default_prompts

APP_TITLE = "Prompt & Config Lab"

# Default ``vite build`` output location. From src/api/app.py, parents[1] is src/, so the
# SPA build lives at src/app/dist. The build isn't committed, so this may be absent on a
# fresh checkout (handled below).
DEFAULT_SPA_DIST = Path(__file__).resolve().parents[1] / "app" / "dist"


def create_app(engine: Engine | None = None, spa_dist: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.engine)
        with Session(app.state.engine) as session:
            ModelCatalogService(session).seed_defaults()
            seed_default_prompts(session)
        yield

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.state.engine = engine or make_engine()

    # /api routers are registered first so JSON routes always win over the SPA catch-all.
    for router in all_routers:
        app.include_router(router)
    _mount_spa(app, spa_dist or DEFAULT_SPA_DIST)

    return app


def _is_api_path(full_path: str) -> bool:
    """True for the ``/api`` prefix so an unmatched ``/api/*`` path 404s as JSON rather than
    falling through to the SPA shell."""
    return full_path == "api" or full_path.startswith("api/")


def _mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built React SPA (ADR-0010 contract step, ticket 08).

    Hashed JS/CSS/font bundles are served from ``/assets``; every other non-``/api`` path
    returns ``index.html`` so react-router owns client-side routing — deep links and hard
    refreshes on ``/runs``, ``/results/{id}``, ``/library/...`` all load the shell.

    The build isn't committed, so a checkout that hasn't run ``vite build`` has no ``dist``.
    Rather than crash at startup, the catch-all serves a plain-text hint until a build
    appears (``index`` is re-checked per request); the JSON API works regardless and dev
    normally runs the Vite dev server anyway.
    """
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:
        # /api routes are registered first and win; an unmatched /api/* path lands here —
        # 404 as JSON rather than silently falling through to the SPA shell.
        if _is_api_path(full_path):
            raise HTTPException(status_code=404, detail="Not found")
        if index.exists():
            return FileResponse(index)
        return PlainTextResponse(
            "SPA build not found. Run `npm run build` in ./src/app, or use the Vite "
            "dev server (`npm run dev`) during development.",
            status_code=503,
        )


app = create_app()
