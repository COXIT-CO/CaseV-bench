"""JSON contract for ground-truth entry (spec §A.6, ticket 07). The twins of the retired
Jinja counting form, plus the brand-new COCO import that surfaces ticket-10's importer
(previously CLI-only) as a Library upload with a problem report.

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
def page_dims(engine, drawing_id) -> dict[int, tuple[int, int]]:
    """The ingested Pages' pixel dims, keyed by page number, so a COCO fixture can point at
    a page that really exists (the importer normalizes against these dims)."""
    from sqlmodel import select

    from core.models.drawing import Page

    with Session(engine) as session:
        return {
            page.page_number: (page.width_px, page.height_px)
            for page in session.exec(
                select(Page).where(Page.drawing_id == drawing_id)
            ).all()
        }


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


# --- location ground truth (COCO import) ----------------------------------------------


def _coco(page_dims: dict[int, tuple[int, int]]) -> dict:
    """A COCO doc with one valid cabinets box on page 1, one unmapped label, and one box on
    a page the drawing does not have — so a single import exercises created + both problem
    kinds."""
    width, height = page_dims[1]
    return {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": width, "height": height},
            {"id": 9, "file_name": "page_0099.png", "width": width, "height": height},
        ],
        "categories": [
            {"id": 1, "name": "cabinet"},
            {"id": 2, "name": "windows"},  # off-taxonomy → unmapped_label
        ],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 20]},
            {"id": 2, "image_id": 1, "category_id": 2, "bbox": [0, 0, 5, 5]},
            {"id": 3, "image_id": 9, "category_id": 1, "bbox": [0, 0, 5, 5]},
        ],
    }


def _upload(coco: dict) -> dict:
    import io
    import json

    return {
        "file": ("gt.json", io.BytesIO(json.dumps(coco).encode()), "application/json")
    }


def test_location_import_creates_boxes_and_reports_problems(
    client, engine, drawing_id, page_dims
):
    response = client.post(
        LOCATION_URL.format(id=drawing_id), files=_upload(_coco(page_dims))
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    kinds = sorted(problem["kind"] for problem in body["problems"])
    assert kinds == ["unknown_page", "unmapped_label"]

    # The valid box actually landed via the service (recompute-on-read makes rows scored).
    with Session(engine) as session:
        assert LocationGroundTruthService(session).boxes_by_page(drawing_id)


def test_location_import_accepts_a_label_map(client, drawing_id, page_dims):
    import json

    width, height = page_dims[1]
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": width, "height": height}
        ],
        "categories": [{"id": 1, "name": "Base Cabinet"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 20]}
        ],
    }
    response = client.post(
        LOCATION_URL.format(id=drawing_id),
        files=_upload(coco),
        data={"label_map": json.dumps({"Base Cabinet": "cabinet"})},
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1


def test_location_import_rejects_non_json_file_with_400(client, drawing_id):
    import io

    response = client.post(
        LOCATION_URL.format(id=drawing_id),
        files={"file": ("gt.json", io.BytesIO(b"not json"), "application/json")},
    )
    assert response.status_code == 400


def test_location_import_rejects_label_map_off_taxonomy_with_400(
    client, drawing_id, page_dims
):
    import json

    response = client.post(
        LOCATION_URL.format(id=drawing_id),
        files=_upload(_coco(page_dims)),
        data={"label_map": json.dumps({"windows": "not_a_real_label"})},
    )
    assert response.status_code == 400


def test_location_import_unknown_drawing_is_404(client, page_dims):
    # A drawing that does not exist; page_dims comes from the fixture drawing only to build
    # a well-formed COCO body.
    response = client.post(LOCATION_URL.format(id=999), files=_upload(_coco(page_dims)))
    assert response.status_code == 404
