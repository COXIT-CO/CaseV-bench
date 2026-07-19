"""JSON contract for ground-truth entry (spec §A.6, ticket 07). The twins of the retired
Jinja counting form, plus the native ``objects`` location import that surfaces the importer
(ADR 0022) as a Library upload with a problem report.

Both endpoints reuse their services unchanged (``CountingGroundTruthService``,
``LocationGroundTruthService``) — only the web layer differs. Entering ground truth turns
the previously-unscored Leaderboard/Result rows into scored ones with no re-run; that
recompute-on-read behavior is the service layer's and is covered by the scoring tests.
"""

import pytest
from sqlmodel import Session

from core.services.counting_ground_truth import CountingGroundTruthService
from core.services.drawing import DrawingService
from core.services.location_ground_truth import LocationGroundTruthService
from core.services.pdf_processing import PDFProcessingService

COUNTING_URL = "/api/drawings/{id}/counting-ground-truth"
LOCATION_URL = "/api/drawings/{id}/location-ground-truth"

ALL_LABELS = ("cabinet", "countertop", "elevation", "elevation_callout")


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


# --- counting ground truth ------------------------------------------------------------


def test_counting_get_prefills_every_label_with_null_when_unentered(client, drawing_id):
    body = client.get(COUNTING_URL.format(id=drawing_id)).json()

    assert body["drawing_id"] == drawing_id
    # One entry per taxonomy label, in the fixed taxonomy order, all null before entry.
    assert [label["name"] for label in body["labels"]] == list(ALL_LABELS)
    assert all(label["value"] is None for label in body["labels"])


def test_counting_get_unknown_drawing_is_404(client):
    response = client.get(COUNTING_URL.format(id=999))
    assert response.status_code == 404
    assert response.json() == {"detail": "Drawing not found"}


def test_counting_put_upserts_and_returns_saved_totals(client, engine, drawing_id):
    response = client.put(
        COUNTING_URL.format(id=drawing_id),
        json={"cabinet": 4, "countertop": 2, "elevation": 1, "elevation_callout": 0},
    )

    assert response.status_code == 200
    values = {label["name"]: label["value"] for label in response.json()["labels"]}
    assert values == {
        "cabinet": 4,
        "countertop": 2,
        "elevation": 1,
        "elevation_callout": 0,
    }
    # Persisted through the service, so a fresh GET pre-fills the saved totals.
    with Session(engine) as session:
        assert CountingGroundTruthService(session).get_totals(drawing_id) == values


def test_counting_put_edits_existing_totals_in_place(client, drawing_id):
    client.put(
        COUNTING_URL.format(id=drawing_id),
        json={"cabinet": 7, "countertop": 0, "elevation": 0, "elevation_callout": 0},
    )
    client.put(
        COUNTING_URL.format(id=drawing_id),
        json={"cabinet": 9, "countertop": 1, "elevation": 0, "elevation_callout": 0},
    )

    prefill = client.get(COUNTING_URL.format(id=drawing_id)).json()
    values = {label["name"]: label["value"] for label in prefill["labels"]}
    assert values["cabinet"] == 9 and values["countertop"] == 1


def test_counting_put_missing_a_label_is_400(client, drawing_id):
    response = client.put(
        COUNTING_URL.format(id=drawing_id),
        json={"cabinet": 4, "countertop": 2, "elevation": 1},  # no elevation_callout
    )
    assert response.status_code == 400


def test_counting_put_non_integer_is_400(client, drawing_id):
    response = client.put(
        COUNTING_URL.format(id=drawing_id),
        json={
            "cabinet": "lots",
            "countertop": 2,
            "elevation": 1,
            "elevation_callout": 0,
        },
    )
    assert response.status_code == 400


def test_counting_put_float_is_rejected_not_truncated(client, drawing_id):
    # A fractional total is a mistake, not something to silently truncate to 2.
    response = client.put(
        COUNTING_URL.format(id=drawing_id),
        json={
            "cabinet": 2.7,
            "countertop": 2,
            "elevation": 1,
            "elevation_callout": 0,
        },
    )
    assert response.status_code == 400


def test_counting_put_unknown_drawing_is_404(client):
    response = client.put(
        COUNTING_URL.format(id=999),
        json={"cabinet": 0, "countertop": 0, "elevation": 0, "elevation_callout": 0},
    )
    assert response.status_code == 404


# --- location ground truth (native objects import) ------------------------------------


def _objects(page_dims: dict[int, tuple[float, float]]) -> dict:
    """A native ``objects`` doc with one valid cabinet box on page 1, one off-taxonomy
    category, one box on a page the drawing does not have, and one box that grossly overflows
    the page's native frame — so a single import exercises created + all three problem kinds.
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
    assert kinds == ["out_of_frame", "unknown_page", "unmapped_label"]

    # The valid box actually landed via the service (recompute-on-read makes rows scored).
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
