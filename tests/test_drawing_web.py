"""Web-layer checks for drawings: list shows page counts, detail shows page
images, images are served, and an upload ingests through the endpoint."""

import pytest
from sqlmodel import Session

from services.drawing import DrawingService
from services.pdf_processing import PDFProcessingService
from web.app import get_drawing_service


@pytest.fixture
def ingested_drawing_id(engine, sample_pdf, tmp_path) -> int:
    with Session(engine) as session:
        service = DrawingService(
            session,
            pdf_service=PDFProcessingService(dpi=72),
            cache_root=tmp_path / "drawings",
        )
        return service.ingest(sample_pdf, name="sample").id


def test_list_shows_drawing_with_page_count(client, ingested_drawing_id):
    response = client.get("/drawings")
    assert response.status_code == 200
    assert "sample" in response.text
    assert "2" in response.text  # page count


def test_detail_shows_page_images(client, ingested_drawing_id):
    response = client.get(f"/drawings/{ingested_drawing_id}")
    assert response.status_code == 200
    assert f"/drawings/{ingested_drawing_id}/pages/1/image" in response.text
    assert f"/drawings/{ingested_drawing_id}/pages/2/image" in response.text


def test_page_image_is_served_as_png(client, ingested_drawing_id):
    response = client.get(f"/drawings/{ingested_drawing_id}/pages/1/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_unknown_drawing_returns_404(client):
    assert client.get("/drawings/999").status_code == 404


def test_upload_ingests_pdf_and_lists_it(client, app, engine, sample_pdf, tmp_path):
    def fast_service():
        with Session(engine) as session:
            yield DrawingService(
                session,
                pdf_service=PDFProcessingService(dpi=72),
                cache_root=tmp_path / "drawings",
            )

    app.dependency_overrides[get_drawing_service] = fast_service

    with sample_pdf.open("rb") as pdf:
        response = client.post(
            "/drawings",
            files={"file": ("sample.pdf", pdf, "application/pdf")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert "sample" in client.get("/drawings").text
