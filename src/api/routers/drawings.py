"""Library — Drawings (spec §A.6): the list, PDF-or-image upload, the detail with rendered
Pages, and the cached page-image PNG re-mounted under ``/api`` for the SPA. Ingestion reuses
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
from core.services.drawing import SUPPORTED_SUFFIXES, DrawingService
from core.services.location_ground_truth import LocationGroundTruthService
from core.utils import ground_truth_overlay_png

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
    (the basis for prediction overlays/display), and the URL of its cached image PNG."""

    page_number: int
    width_px: int
    height_px: int
    image_url: str


class DrawingDetailResponse(BaseModel):
    """``GET /api/drawings/{id}``: the Drawing plus its rendered Pages. The detail is where
    ground-truth entry hangs off (ticket 07) and where the Drawing can be deleted (ticket
    08) — so it carries the delete's collateral counts (the Runs + Results that used this
    Drawing) up front, letting the confirm dialog state the blast radius before committing
    (ADR-0016)."""

    drawing: DrawingRef
    pages: list[DrawingPageOut]
    run_count: int
    result_count: int


class DrawingDeletedOut(BaseModel):
    """The collateral ``DELETE /api/drawings/{id}`` removed (ADR-0016): how many Runs and
    Results the cascade took with the Drawing — the delete's receipt, and the same
    ``(runs, results)`` shape the Run delete returns."""

    runs: int
    results: int


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
    """Ingest an uploaded PDF or plain image (PNG/JPG/WebP) via ``DrawingService`` and return
    the created Drawing (spec §A.6, ticket 11). One unified control accepts either kind; the
    service branches on file type. Upload stays ``multipart/form-data``; the file is written
    to a temp file preserving its extension (so the service can tell an image from a PDF),
    then removed. An unsupported type is a ``415`` before anything touches the DB."""
    filename = Path(file.filename or "drawing")
    suffix = filename.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported file type — upload a PDF or an image "
                "(PNG, JPG, or WebP)."
            ),
        )
    name = filename.stem or "drawing"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
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
    drawing_id: int,
    session: Session = Depends(get_session),
    service: DrawingService = Depends(get_drawing_service),
) -> DrawingDetailResponse:
    """The Drawing's rendered Pages with pixel dims + image URLs (spec §A.6). The page
    image URLs point at the ``/api`` PNG route so the SPA fetches under one origin. Also
    carries the delete's collateral counts (the service computes them, ADR-0015/0016) so the
    confirm dialog can state the blast radius. An unknown Drawing is a ``404``."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    collateral = service.collateral_counts(drawing_id)
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
        run_count=collateral.runs,
        result_count=collateral.results,
    )


@router.delete("/drawings/{drawing_id}", response_model=DrawingDeletedOut)
def delete_drawing(
    drawing_id: int,
    service: DrawingService = Depends(get_drawing_service),
) -> DrawingDeletedOut:
    """Permanently delete a Drawing and everything derived from it — its Pages, both kinds of
    ground truth, its cached page images, and every Run/Result that used it (so they leave the
    Leaderboard too) (ADR-0016). Returns the collateral counts the confirm dialog showed; a
    missing Drawing is a ``404``."""
    try:
        counts = service.delete_drawing(drawing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return DrawingDeletedOut(runs=counts.runs, results=counts.results)


def _require_page_with_image(
    session: Session, drawing_id: int, page_number: int
) -> Page:
    """The Page for a (Drawing, page_number) whose cached image exists, or a ``404`` — the
    shared lookup both page-image PNG routes below serve, so their not-found rule stays in
    one place."""
    page = session.exec(
        select(Page).where(
            Page.drawing_id == drawing_id, Page.page_number == page_number
        )
    ).first()
    if page is None or not Path(page.image_path).exists():
        raise HTTPException(status_code=404, detail="Page image not found")
    return page


@router.get("/drawings/{drawing_id}/pages/{page_number}/image")
def drawing_page_image(
    drawing_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> Response:
    """The cached, downsampled page image PNG for one (Drawing, Page), served under
    ``/api`` for the SPA (ported unchanged from the retired Jinja twin)."""
    page = _require_page_with_image(session, drawing_id, page_number)
    return FileResponse(page.image_path, media_type="image/png")


@router.get("/drawings/{drawing_id}/pages/{page_number}/gt-overlay")
def drawing_page_gt_overlay(
    drawing_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> Response:
    """The ground-truth overlay PNG for one (Drawing, Page) — the backend half of the Library
    verification view (ADR 0024, ticket 05). The Page's imported LocationGroundTruth boxes are
    drawn on the page image, each in its ObjectType's colour (the shared palette + legend the
    prediction overlay uses), rendered on demand from the stored normalized boxes so the overlay
    always reflects the latest import. A page with no ground truth returns the plain page image
    (no boxes, no error); an unknown page or a missing image file is a ``404`` (mirrors the plain
    page-image route)."""
    page = _require_page_with_image(session, drawing_id, page_number)
    rows = (
        LocationGroundTruthService(session).boxes_by_page(drawing_id).get(page.id, [])
    )
    png = ground_truth_overlay_png(Path(page.image_path), rows)
    return Response(content=png, media_type="image/png")
