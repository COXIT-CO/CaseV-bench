"""JSON contract for the Library (spec §A.6, ticket 06). These are the twins of the Jinja
``/drawings`` list, its upload form, the ``/drawings/{id}`` detail page (page thumbnails +
pixel dims), the cached page-image PNG route re-mounted under ``/api``, and the ``/models``
curated catalog. Ingestion reuses ``DrawingService`` unchanged — only the web layer differs.

Ground-truth entry (counting form, native objects import) is ticket 07 and is not covered here; the
Drawing detail is only the entry point it hangs off.
"""

import pytest
from sqlmodel import Session, select

from api.deps import get_drawing_service
from core.models.prompt import Prompt, Task
from core.models.run import Result, Run
from core.services.drawing import DrawingService
from core.services.pdf_processing import PDFProcessingService

SONNET = "anthropic/claude-sonnet-4.5"


def _add_run(engine, drawing_id: int, model: str = SONNET) -> int:
    """Attach a done Run (with one Result) to a Drawing so the delete has collateral. The
    counting prompt family is seeded on startup, so a Run can pin it directly."""
    with Session(engine) as session:
        prompt = session.exec(
            select(Prompt).where(Prompt.task == Task.counting)
        ).first()
        run = Run(
            task=Task.counting,
            prompt_id=prompt.id,
            drawing_id=drawing_id,
            dpi=200,
            downsample_px=1600,
            max_tokens=4096,
            temperature=0.0,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(Result(run_id=run.id, model=model))
        session.commit()
        return run.id


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


@pytest.fixture
def delete_capable_service(app, engine, tmp_path):
    """Override the delete route's service so its cascade cleans on-disk artifacts under the
    test's temp roots (the same ``drawings`` root ``ingested_drawing_id`` renders into).
    """

    def _svc():
        with Session(engine) as session:
            yield DrawingService(
                session,
                cache_root=tmp_path / "drawings",
                overlay_root=tmp_path / "overlays",
            )

    app.dependency_overrides[get_drawing_service] = _svc
    return _svc


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


def test_upload_ingests_image_as_single_page_drawing(
    client, fast_drawing_service, sample_image
):
    # One unified upload control accepts an image just like a PDF and yields a one-page
    # Drawing (ticket 11).
    with sample_image.open("rb") as image:
        response = client.post(
            "/api/drawings",
            files={"file": ("sample.png", image, "image/png")},
        )

    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "sample"
    assert created["page_count"] == 1
    assert created in client.get("/api/drawings").json()["drawings"]


def test_upload_rejects_unsupported_file_type(client, fast_drawing_service):
    response = client.post(
        "/api/drawings",
        files={"file": ("notes.txt", b"not a drawing", "text/plain")},
    )

    assert response.status_code == 415
    assert client.get("/api/drawings").json()["drawings"] == []


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
    assert first["image_url"] == f"/api/drawings/{ingested_drawing_id}/pages/1/image"
    assert body["pages"][0]["width_px"] != body["pages"][1]["width_px"]
    # No Runs use this Drawing yet, so the delete-collateral counts are zero (ADR-0016).
    assert body["run_count"] == 0
    assert body["result_count"] == 0


def test_detail_reports_delete_collateral_counts(client, engine, ingested_drawing_id):
    _add_run(engine, ingested_drawing_id)

    body = client.get(f"/api/drawings/{ingested_drawing_id}").json()

    # The confirm dialog needs the Runs/Results that would be cascaded, stated up front.
    assert body["run_count"] == 1
    assert body["result_count"] == 1


def test_delete_drawing_cascades_and_returns_counts(
    client, engine, ingested_drawing_id, delete_capable_service, tmp_path
):
    _add_run(engine, ingested_drawing_id)
    page_dir = tmp_path / "drawings" / str(ingested_drawing_id)
    assert page_dir.is_dir()  # ingestion rendered the page images here

    resp = client.delete(f"/api/drawings/{ingested_drawing_id}")
    assert resp.status_code == 200
    assert resp.json() == {"runs": 1, "results": 1}

    # The Drawing is gone from both the detail endpoint and the list, and its run with it.
    assert client.get(f"/api/drawings/{ingested_drawing_id}").status_code == 404
    assert client.get("/api/drawings").json() == {"drawings": []}
    assert not page_dir.exists()
    with Session(engine) as session:
        assert session.exec(select(Run)).all() == []


def test_delete_drawing_missing_404(client):
    assert client.delete("/api/drawings/999").status_code == 404


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


PIXTRAL = "mistralai/pixtral-12b"


def test_add_model_appears_in_catalog_and_launch_options(client):
    resp = client.post("/api/models", json={"slug": PIXTRAL, "label": "Pixtral 12B"})
    assert resp.status_code == 201
    assert resp.json() == {"slug": PIXTRAL, "label": "Pixtral 12B"}

    # A newly added entry is offered on every future launch (ticket 10): it shows in both
    # the catalog view and the launch-form option set.
    catalog = client.get("/api/models").json()["catalog"]
    assert {"slug": PIXTRAL, "label": "Pixtral 12B"} in catalog
    launch_catalog = client.get("/api/runs/launch-options").json()["catalog"]
    assert {"slug": PIXTRAL, "label": "Pixtral 12B"} in launch_catalog


def test_add_existing_slug_upserts_label(client):
    client.post("/api/models", json={"slug": PIXTRAL, "label": "Pixtral 12B"})
    client.post("/api/models", json={"slug": PIXTRAL, "label": "Pixtral (renamed)"})

    catalog = client.get("/api/models").json()["catalog"]
    matching = [e for e in catalog if e["slug"] == PIXTRAL]
    assert matching == [{"slug": PIXTRAL, "label": "Pixtral (renamed)"}]  # no dupe


def test_add_model_rejects_blank_label(client):
    resp = client.post("/api/models", json={"slug": PIXTRAL, "label": "   "})
    assert resp.status_code == 400


def test_remove_model_drops_it_from_catalog(client):
    client.post("/api/models", json={"slug": PIXTRAL, "label": "Pixtral 12B"})

    resp = client.request("DELETE", f"/api/models/{PIXTRAL}")
    assert resp.status_code == 200
    assert resp.json() == {"slug": PIXTRAL, "label": "Pixtral 12B"}

    slugs = {e["slug"] for e in client.get("/api/models").json()["catalog"]}
    assert PIXTRAL not in slugs
    launch_slugs = {
        e["slug"] for e in client.get("/api/runs/launch-options").json()["catalog"]
    }
    assert PIXTRAL not in launch_slugs


def test_remove_unknown_model_is_404(client):
    assert client.request("DELETE", "/api/models/nope/not-a-model").status_code == 404
