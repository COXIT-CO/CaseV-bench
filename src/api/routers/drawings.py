"""Library — Drawings (spec §A.6): the list, PDF upload, the detail with rendered Pages,
and the cached page-image PNG re-mounted under ``/api`` for the SPA. Ingestion reuses
``DrawingService`` unchanged; only the web layer differs. Ground-truth entry hangs off the
detail (see ``ground_truth`` router)."""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from api.deps import get_drawing_service, get_session
from api.routers.common import DrawingRef
from core.models.drawing import Drawing, Page
from core.services.drawing import DrawingService

router = APIRouter(prefix="/api", tags=["drawings"])


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


@router.get("/drawings", response_model=DrawingsResponse)
def list_drawings(session: Session = Depends(get_session)) -> DrawingsResponse:
    """Every Drawing with its page count, newest-first (spec §A.6)."""
    drawings = session.exec(select(Drawing).order_by(Drawing.created_at.desc())).all()
    return DrawingsResponse(
        drawings=[
            DrawingSummary(id=d.id, name=d.name, page_count=len(d.pages))
            for d in drawings
        ]
    )


@router.post("/drawings", response_model=DrawingSummary, status_code=201)
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


@router.get("/drawings/{drawing_id}", response_model=DrawingDetailResponse)
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


@router.get("/drawings/{drawing_id}/pages/{page_number}/image")
def drawing_page_image(
    drawing_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> Response:
    """The cached, downsampled page image PNG for one (Drawing, Page), served under
    ``/api`` for the SPA (ported unchanged from the retired Jinja twin)."""
    page = session.exec(
        select(Page).where(
            Page.drawing_id == drawing_id, Page.page_number == page_number
        )
    ).first()
    if page is None or not Path(page.image_path).exists():
        raise HTTPException(status_code=404, detail="Page image not found")
    return FileResponse(page.image_path, media_type="image/png")
