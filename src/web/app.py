"""FastAPI app serving Jinja2 templates with HTMX (ADR 0005).

``create_app()`` is a factory so tests can build the app against a temp SQLite engine
and override the OpenRouter adapter. The schema is created on startup (ADR 0008).
"""

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlmodel import Session, select

from db import get_session, init_db, make_engine
from models.drawing import Drawing, Page
from services.drawing import DrawingService

APP_TITLE = "Prompt & Config Lab"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_drawing_service(session: Session = Depends(get_session)) -> DrawingService:
    """FastAPI dependency; override in tests to inject a fast/low-DPI service."""
    return DrawingService(session)


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

    return app


app = create_app()
