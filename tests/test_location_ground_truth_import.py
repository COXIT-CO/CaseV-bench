"""COCO importer for LocationGroundTruth (ticket 10, ADR 0003).

Pure-ish service tests over a temp SQLite DB: a COCO JSON's absolute pixel boxes are
converted to normalized 0-1 boxes on the correct Pages, external labels are mapped onto
the fixed taxonomy, and an unmapped label / unknown page is reported rather than silently
dropped. The importer never calls a model, so no adapter seam is involved.
"""

import pytest
from sqlmodel import select

from models.drawing import Drawing, Page
from models.location_ground_truth import LocationGroundTruth
from services.location_ground_truth import (
    UNKNOWN_PAGE,
    UNMAPPED_LABEL,
    LocationGroundTruthService,
)


def _make_drawing_with_pages(session, dims: list[tuple[int, int]]) -> Drawing:
    """A Drawing with one Page per (width_px, height_px), numbered from 1."""
    drawing = Drawing(name="d")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    for page_number, (width_px, height_px) in enumerate(dims, start=1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=f"page_{page_number:04d}_downsampled.png",
                width_px=width_px,
                height_px=height_px,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def _boxes_by_page(session, drawing: Drawing) -> dict[int, list[LocationGroundTruth]]:
    grouped: dict[int, list[LocationGroundTruth]] = {}
    for page in drawing.pages:
        rows = session.exec(
            select(LocationGroundTruth).where(LocationGroundTruth.page_id == page.id)
        ).all()
        grouped[page.page_number] = list(rows)
    return grouped


def test_import_creates_normalized_boxes_on_correct_pages(session):
    drawing = _make_drawing_with_pages(session, [(1000, 2000), (500, 400)])
    coco = {
        "images": [
            {"id": 11, "file_name": "page_0001.png", "width": 1000, "height": 2000},
            {"id": 22, "file_name": "page_0002.png", "width": 500, "height": 400},
        ],
        "categories": [
            {"id": 1, "name": "cabinets"},
            {"id": 2, "name": "countertops"},
        ],
        "annotations": [
            # page 1: [x, y, w, h] px -> normalized by (1000, 2000)
            {"id": 1, "image_id": 11, "category_id": 1, "bbox": [100, 200, 300, 400]},
            # page 2: normalized by (500, 400)
            {"id": 2, "image_id": 22, "category_id": 2, "bbox": [50, 40, 100, 80]},
        ],
    }

    result = LocationGroundTruthService(session).import_coco(drawing.id, coco)

    assert result.created == 2
    assert result.problems == []

    by_page = _boxes_by_page(session, drawing)
    assert len(by_page[1]) == 1 and len(by_page[2]) == 1

    box1 = by_page[1][0]
    assert box1.label == "cabinets"
    assert box1.x_min == pytest.approx(0.1)
    assert box1.y_min == pytest.approx(0.1)
    assert box1.x_max == pytest.approx(0.4)  # (100 + 300) / 1000
    assert box1.y_max == pytest.approx(0.3)  # (200 + 400) / 2000

    box2 = by_page[2][0]
    assert box2.label == "countertops"
    assert box2.x_min == pytest.approx(0.1)  # 50 / 500
    assert box2.y_min == pytest.approx(0.1)  # 40 / 400
    assert box2.x_max == pytest.approx(0.3)  # (50 + 100) / 500
    assert box2.y_max == pytest.approx(0.3)  # (40 + 80) / 400


def test_normalization_uses_page_dimensions_not_coco_image_dims(session):
    # The Page's stored dims are authoritative (ticket 10). If a COCO image reports
    # different dims, normalization must still divide by the Page's dimensions.
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": 500, "height": 500},
        ],
        "categories": [{"id": 1, "name": "cabinets"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [100, 100, 200, 200]},
        ],
    }

    LocationGroundTruthService(session).import_coco(drawing.id, coco)

    box = _boxes_by_page(session, drawing)[1][0]
    # Divided by the Page's 1000, not the COCO image's 500.
    assert box.x_min == pytest.approx(0.1)
    assert box.x_max == pytest.approx(0.3)


def test_external_label_names_are_mapped_onto_taxonomy(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": 1000, "height": 1000}
        ],
        "categories": [{"id": 7, "name": "Base Cabinet"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 7, "bbox": [0, 0, 100, 100]},
        ],
    }

    result = LocationGroundTruthService(session).import_coco(
        drawing.id, coco, label_map={"Base Cabinet": "cabinets"}
    )

    assert result.created == 1
    assert result.problems == []
    assert _boxes_by_page(session, drawing)[1][0].label == "cabinets"


def test_unmapped_label_is_reported_not_dropped(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": 1000, "height": 1000}
        ],
        "categories": [
            {"id": 1, "name": "cabinets"},
            {"id": 2, "name": "windows"},  # not in the taxonomy or the map
        ],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 100, 100]},
            {"id": 2, "image_id": 1, "category_id": 2, "bbox": [0, 0, 100, 100]},
        ],
    }

    result = LocationGroundTruthService(session).import_coco(drawing.id, coco)

    # The mappable annotation still imports; the unmapped one is reported, not dropped silently.
    assert result.created == 1
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.kind == UNMAPPED_LABEL
    assert "windows" in problem.detail


def test_label_map_targeting_outside_taxonomy_is_rejected(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": 1000, "height": 1000}
        ],
        "categories": [{"id": 1, "name": "Base Cabinet"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 10, 10]}
        ],
    }
    with pytest.raises(ValueError):
        LocationGroundTruthService(session).import_coco(
            drawing.id, coco, label_map={"Base Cabinet": "windows"}
        )


def test_unknown_page_is_reported_not_dropped(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])  # only page 1 exists
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": 1000, "height": 1000},
            {"id": 2, "file_name": "page_0009.png", "width": 1000, "height": 1000},
        ],
        "categories": [{"id": 1, "name": "cabinets"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 100, 100]},
            {"id": 2, "image_id": 2, "category_id": 1, "bbox": [0, 0, 100, 100]},
        ],
    }

    result = LocationGroundTruthService(session).import_coco(drawing.id, coco)

    assert result.created == 1
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.kind == UNKNOWN_PAGE
    assert "9" in problem.detail


def test_reimport_replaces_rather_than_duplicates(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": 1000, "height": 1000}
        ],
        "categories": [{"id": 1, "name": "cabinets"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 100, 100]},
        ],
    }
    service = LocationGroundTruthService(session)

    service.import_coco(drawing.id, coco)
    service.import_coco(drawing.id, coco)

    assert len(_boxes_by_page(session, drawing)[1]) == 1
