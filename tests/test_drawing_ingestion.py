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


def test_ingest_stores_native_point_dims_from_source_pdf(session, sample_pdf, tmp_path):
    # Render above 72 DPI so the full-resolution raster dims differ from the native point
    # dims, proving native dims come from ``page.rect`` (72-DPI point space, 1 px = 1 pt)
    # and not from the rendered raster (ADR 0022).
    service = DrawingService(
        session,
        pdf_service=PDFProcessingService(dpi=144),
        cache_root=tmp_path / "drawings",
    )

    service.ingest(sample_pdf, name="sample")

    pages = session.exec(select(Page).order_by(Page.page_number)).all()
    # sample_pdf: page 1 is 612×792 pt (portrait), page 2 is 792×612 pt (landscape).
    assert (pages[0].native_width_pt, pages[0].native_height_pt) == (612, 792)
    assert (pages[1].native_width_pt, pages[1].native_height_pt) == (792, 612)
    # The full-resolution px dims are the 144-DPI raster — twice the point dims — and keep
    # their existing meaning (prediction overlays/display), independent of the native frame.
    assert (pages[0].width_px, pages[0].height_px) == (1224, 1584)
    assert (pages[1].width_px, pages[1].height_px) == (1584, 1224)


def test_ingest_image_has_no_native_point_dims(session, sample_image, tmp_path):
    # An image-ingested Page has no source PDF, so there is no native point frame. Recording
    # it as None lets the native GT importer detect "no native frame available" and reject
    # the import (ADR 0022).
    service = _fast_service(session, tmp_path)

    service.ingest(sample_image, name="photo")

    page = session.exec(select(Page)).one()
    assert page.native_width_pt is None
    assert page.native_height_pt is None


def test_ingest_renders_and_downsamples_each_page_once(session, sample_pdf, tmp_path):
    service = _fast_service(session, tmp_path)

    drawing = service.ingest(sample_pdf, name="sample")

    pages = session.exec(select(Page).order_by(Page.page_number)).all()
    cached = [Path(p.image_path) for p in pages]
    # One cached image per page, all present and distinct.
    assert all(p.exists() for p in cached)
    assert len(set(cached)) == len(cached)
