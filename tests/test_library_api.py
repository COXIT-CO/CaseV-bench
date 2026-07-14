"""JSON contract for the Library (spec §A.6, ticket 06). These are the twins of the Jinja
``/drawings`` list, its upload form, the ``/drawings/{id}`` detail page (page thumbnails +
pixel dims), the cached page-image PNG route re-mounted under ``/api``, and the ``/models``
curated catalog. Ingestion reuses ``DrawingService`` unchanged — only the web layer differs.

Ground-truth entry (counting form, COCO import) is ticket 07 and is not covered here; the
Drawing detail is only the entry point it hangs off.
"""

import pytest
from sqlmodel import Session

from services.drawing import DrawingService
from services.pdf_processing import PDFProcessingService
from web.api import get_drawing_service


@pytest.fixture
def ingested_drawing_id(engine, sample_pdf, tmp_path) -> int:
    with Session(engine) as session:
        service = DrawingService(
            session,
            pdf_service=PDFProcessingService(dpi=72),
            cache_root=tmp_path / "drawings",
        )
        return service.ingest(sample_pdf, name="sample").id


@pytest.fixture
def fast_drawing_service(app, engine, tmp_path):
    """Override the upload route's service with a low-DPI one so the multipart ingest in a
    test renders quickly (mirrors the Jinja upload test's override)."""

    def _fast():
        with Session(engine) as session:
            yield DrawingService(
                session,
                pdf_service=PDFProcessingService(dpi=72),
                cache_root=tmp_path / "drawings",
            )

    app.dependency_overrides[get_drawing_service] = _fast
    return _fast


def test_list_returns_drawings_with_page_counts(client, ingested_drawing_id):
    body = client.get("/api/drawings").json()

    assert body["drawings"] == [
        {"id": ingested_drawing_id, "name": "sample", "page_count": 2}
    ]


def test_list_is_empty_when_no_drawings(client):
    assert client.get("/api/drawings").json() == {"drawings": []}


def test_upload_ingests_pdf_and_returns_created_drawing(
    client, fast_drawing_service, sample_pdf
):
    with sample_pdf.open("rb") as pdf:
        response = client.post(
            "/api/drawings",
            files={"file": ("sample.pdf", pdf, "application/pdf")},
        )

    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "sample"
    assert created["page_count"] == 2
    # The created Drawing now appears in the list.
    listed = client.get("/api/drawings").json()["drawings"]
    assert created in listed


def test_detail_returns_pages_with_pixel_dims_and_image_urls(
    client, ingested_drawing_id
):
    body = client.get(f"/api/drawings/{ingested_drawing_id}").json()

    assert body["drawing"] == {"id": ingested_drawing_id, "name": "sample"}
    assert len(body["pages"]) == 2
    first = body["pages"][0]
    assert first["page_number"] == 1
    # The sample PDF's two pages have distinct sizes, so real per-page dims are recorded.
    assert first["width_px"] > 0 and first["height_px"] > 0
    assert (
        first["image_url"]
        == f"/api/drawings/{ingested_drawing_id}/pages/1/image"
    )
    assert body["pages"][0]["width_px"] != body["pages"][1]["width_px"]


def test_detail_unknown_drawing_is_404(client):
    response = client.get("/api/drawings/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Drawing not found"}


def test_page_image_is_served_as_png(client, ingested_drawing_id):
    response = client.get(f"/api/drawings/{ingested_drawing_id}/pages/1/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_page_image_unknown_page_is_404(client, ingested_drawing_id):
    response = client.get(f"/api/drawings/{ingested_drawing_id}/pages/99/image")
    assert response.status_code == 404
    assert response.json() == {"detail": "Page image not found"}


def test_models_returns_curated_catalog(client):
    body = client.get("/api/models").json()

    slugs = {entry["slug"] for entry in body["catalog"]}
    assert "anthropic/claude-sonnet-4.5" in slugs
    # Every entry carries the display label the catalog was seeded with.
    for entry in body["catalog"]:
        assert set(entry) == {"slug", "label"}
        assert entry["label"]
