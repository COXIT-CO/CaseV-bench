"""Ingestion integration test (spec: Testing Decisions) — a fixture PDF ingested
against a temp SQLite DB persists the expected Drawing + Pages with dimensions.
"""

from pathlib import Path

from PIL import Image
from sqlmodel import select

from core.models.drawing import Drawing, Page
from core.services.drawing import DrawingService
from core.services.pdf_processing import PDFProcessingService
from core.utils import DEFAULT_DOWNSAMPLE_PX


def _fast_service(session, tmp_path) -> DrawingService:
    # Low DPI keeps the render fast; a temp cache dir keeps data/ untouched.
    return DrawingService(
        session,
        pdf_service=PDFProcessingService(dpi=72),
        cache_root=tmp_path / "drawings",
    )


def test_ingest_creates_drawing_and_pages_with_dimensions(
    session, sample_pdf, tmp_path
):
    service = _fast_service(session, tmp_path)

    drawing = service.ingest(sample_pdf, name="sample")

    assert drawing.id is not None
    assert session.exec(select(Drawing)).all() == [drawing]

    pages = session.exec(select(Page).order_by(Page.page_number)).all()
    assert len(pages) == 2
    assert [p.page_number for p in pages] == [1, 2]

    for page in pages:
        assert page.drawing_id == drawing.id
        assert page.width_px > 0 and page.height_px > 0
        assert Path(page.image_path).exists()

    # The two fixture pages have distinct orientations, so their cached images do too.
    assert pages[0].width_px < pages[0].height_px  # portrait
    assert pages[1].width_px > pages[1].height_px  # landscape


def test_ingest_image_creates_single_page_drawing_with_dimensions(
    session, sample_image, tmp_path
):
    # A plain image skips PDF rendering and becomes a one-Page Drawing that behaves like a
    # one-page PDF (ticket 11). Its native pixel dims are stored (so location GT import can
    # normalize boxes), and it is still downsampled to the standard long edge.
    service = _fast_service(session, tmp_path)

    drawing = service.ingest(sample_image, name="photo")

    assert drawing.id is not None
    pages = session.exec(select(Page)).all()
    assert len(pages) == 1
    page = pages[0]
    assert page.drawing_id == drawing.id
    assert page.page_number == 1
    # Native dimensions of the uploaded image (2000×1500), not the downsample.
    assert (page.width_px, page.height_px) == (2000, 1500)

    cached = Path(page.image_path)
    assert cached.exists()
    with Image.open(cached) as image:
        # Downsampled to the standard long edge, preserving aspect ratio.
        assert max(image.size) == DEFAULT_DOWNSAMPLE_PX


def test_ingest_renders_and_downsamples_each_page_once(session, sample_pdf, tmp_path):
    service = _fast_service(session, tmp_path)

    drawing = service.ingest(sample_pdf, name="sample")

    pages = session.exec(select(Page).order_by(Page.page_number)).all()
    cached = [Path(p.image_path) for p in pages]
    # One cached image per page, all present and distinct.
    assert all(p.exists() for p in cached)
    assert len(set(cached)) == len(cached)
