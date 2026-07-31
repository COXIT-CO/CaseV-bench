"""JSON contract for ground-truth entry (spec §A.6, ticket 07): the native ``objects``
location import that surfaces the importer (ADR 0022) as a Library upload with a problem
report.

The endpoint reuses ``LocationGroundTruthService`` unchanged — only the web layer differs.
Entering ground truth turns the previously-unscored Leaderboard/Result rows into scored ones
with no re-run; that recompute-on-read behavior is the service layer's and is covered by the
scoring tests.

The retired counting form's endpoints are gone rather than shimmed, so a stale client fails
loudly on a 404 (ADR 0032; spec-drop-counting: API contract changes).
"""

import pytest
from sqlmodel import Session

from core.services.drawing import DrawingService
from core.services.location_ground_truth import LocationGroundTruthService
from core.services.pdf_processing import PDFProcessingService

COUNTING_URL = "/api/drawings/{id}/counting-ground-truth"
LOCATION_URL = "/api/drawings/{id}/location-ground-truth"


@pytest.fixture
def drawing_id(engine, sample_pdf, tmp_path) -> int:
    with Session(engine) as session:
        service = DrawingService(
            session,
            pdf_service=PDFProcessingService(dpi=72),
            cache_root=tmp_path / "drawings",
        )
        return service.ingest(sample_pdf, name="sample").id


@pytest.fixture
def page_dims(engine, drawing_id) -> dict[int, tuple[float, float]]:
    """The ingested Pages' native point dims, keyed by page number, so a native ``objects``
    fixture can point at a page that really exists (the importer normalizes against these
    native dims — ADR 0022, not the render-DPI pixel dims)."""
    from sqlmodel import select

    from core.models.drawing import Page

    with Session(engine) as session:
        return {
            page.page_number: (page.native_width_pt, page.native_height_pt)
            for page in session.exec(
                select(Page).where(Page.drawing_id == drawing_id)
            ).all()
        }


@pytest.fixture
def image_drawing_id(engine, sample_image, tmp_path) -> int:
    """An image-ingested Drawing (no source PDF, so no native page frame) — the importer
    rejects a location import for it with a 400."""
    with Session(engine) as session:
        service = DrawingService(
            session,
            pdf_service=PDFProcessingService(dpi=72),
            cache_root=tmp_path / "image-drawings",
        )
        return service.ingest(sample_image, name="image").id


# --- the removed counting endpoints ---------------------------------------------------


def test_counting_ground_truth_get_is_404(client, drawing_id):
    # Removed rather than shimmed: a stale client asking for counting GT fails loudly
    # instead of silently reaching a stub (ADR 0032).
    assert client.get(COUNTING_URL.format(id=drawing_id)).status_code == 404


def test_counting_ground_truth_put_is_404(client, drawing_id):
    response = client.put(
        COUNTING_URL.format(id=drawing_id),
        json={"cabinet": 4, "countertop": 2, "elevation": 1, "elevation_callout": 0},
    )
    assert response.status_code == 404


# --- location ground truth (native objects import) ------------------------------------


def _objects(page_dims: dict[int, tuple[float, float]]) -> dict:
    """A native ``objects`` doc with one valid cabinet box on page 1, one off-taxonomy
    category, one box on a page the drawing does not have, one box that grossly overflows the
    page's native frame, and one enclosing no area — so a single import exercises created +
    all four problem kinds.
    """
    width, height = page_dims[1]
    return {
        "project_id": "prj1",
        "objects": [
            {
                "id": "a",
                "category": "cabinet",
                "page": 1,
                "bbox": {"x": 10, "y": 10, "width": 20, "height": 20},
            },
            {  # off-taxonomy → unmapped_label
                "id": "b",
                "category": "windows",
                "page": 1,
                "bbox": {"x": 0, "y": 0, "width": 5, "height": 5},
            },
            {  # page 9 does not exist → unknown_page
                "id": "c",
                "category": "cabinet",
                "page": 9,
                "bbox": {"x": 0, "y": 0, "width": 5, "height": 5},
            },
            {  # extends far past the native frame → out_of_frame
                "id": "d",
                "category": "cabinet",
                "page": 1,
                "bbox": {"x": 0, "y": 0, "width": width * 2, "height": height * 2},
            },
            {  # encloses no area → degenerate_box
                "id": "e",
                "category": "cabinet",
                "page": 1,
                "bbox": {"x": 5, "y": 5, "width": 0, "height": 5},
            },
        ],
    }


def _upload(document: dict) -> dict:
    import io
    import json

    return {
        "file": (
            "gt.json",
            io.BytesIO(json.dumps(document).encode()),
            "application/json",
        )
    }


def test_location_import_creates_boxes_and_reports_problems(
    client, engine, drawing_id, page_dims
):
    response = client.post(
        LOCATION_URL.format(id=drawing_id), files=_upload(_objects(page_dims))
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    kinds = sorted(problem["kind"] for problem in body["problems"])
    assert kinds == [
        "degenerate_box",
        "out_of_frame",
        "unknown_page",
        "unmapped_label",
    ]

    # The valid box actually landed via the service (recompute-on-read makes rows scored).
    with Session(engine) as session:
        assert LocationGroundTruthService(session).boxes_by_page(drawing_id)


def test_location_import_ignores_a_derive_counting_field(
    client, engine, drawing_id, page_dims
):
    # The import does exactly one thing now (ADR 0032): a stale client still sending the
    # retired derive flag gets a normal location import, not an error and not a second write.
    response = client.post(
        LOCATION_URL.format(id=drawing_id),
        files=_upload(_objects(page_dims)),
        data={"derive_counting": "true"},
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1
    with Session(engine) as session:
        assert LocationGroundTruthService(session).boxes_by_page(drawing_id)


def test_location_import_rejects_non_json_file_with_400(client, drawing_id):
    import io

    response = client.post(
        LOCATION_URL.format(id=drawing_id),
        files={"file": ("gt.json", io.BytesIO(b"not json"), "application/json")},
    )
    assert response.status_code == 400


def test_location_import_rejects_non_object_body_with_400(client, drawing_id):
    response = client.post(LOCATION_URL.format(id=drawing_id), files=_upload([1, 2, 3]))
    assert response.status_code == 400


def test_location_import_rejects_image_ingested_drawing_with_400(
    client, image_drawing_id, page_dims
):
    # An image-ingested Drawing has no source PDF and thus no native page frame to normalize
    # against; the import is rejected rather than wrongly normalized.
    response = client.post(
        LOCATION_URL.format(id=image_drawing_id), files=_upload(_objects(page_dims))
    )
    assert response.status_code == 400


def test_location_import_unknown_drawing_is_404(client, page_dims):
    # A drawing that does not exist; page_dims comes from the fixture drawing only to build
    # a well-formed native body.
    response = client.post(
        LOCATION_URL.format(id=999), files=_upload(_objects(page_dims))
    )
    assert response.status_code == 404


# --- ground-truth overlay (ticket 05, ADR 0024) ---------------------------------------

GT_OVERLAY_URL = "/api/drawings/{id}/pages/{page}/gt-overlay"


def test_gt_overlay_labeled_page_returns_png(client, drawing_id, page_dims):
    # After an import, page 1 carries a ground-truth box; the route returns its overlay PNG.
    client.post(LOCATION_URL.format(id=drawing_id), files=_upload(_objects(page_dims)))

    response = client.get(GT_OVERLAY_URL.format(id=drawing_id, page=1))

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_gt_overlay_unlabeled_page_returns_plain_page(client, drawing_id):
    # With no import the page has no ground truth; the route returns the plain page image
    # (no boxes, no error) rather than 404-ing.
    response = client.get(GT_OVERLAY_URL.format(id=drawing_id, page=1))

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_gt_overlay_reflects_the_latest_import(client, drawing_id, page_dims):
    # The overlay renders on demand from the stored boxes, so importing boxes onto a page
    # changes what it draws: the labeled overlay differs from the plain (pre-import) page.
    plain = client.get(GT_OVERLAY_URL.format(id=drawing_id, page=1)).content

    client.post(LOCATION_URL.format(id=drawing_id), files=_upload(_objects(page_dims)))
    labeled = client.get(GT_OVERLAY_URL.format(id=drawing_id, page=1)).content

    assert labeled != plain


def test_gt_overlay_unknown_page_is_404(client, drawing_id):
    # A page the drawing does not have (the fixture PDF has 2 pages) → 404, like the plain
    # page-image route.
    response = client.get(GT_OVERLAY_URL.format(id=drawing_id, page=9))

    assert response.status_code == 404
