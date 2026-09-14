"""Native ``objects`` importer for LocationGroundTruth (ticket 03, ADR 0022).

Pure-ish service tests over a temp SQLite DB: a native ``objects`` JSON's absolute pixel
boxes are converted to normalized 0-1 boxes on the correct Pages, normalized by each Page's
**native point dimensions** (the source PDF's ``page.rect``, captured at ingest), and anything
that can't be imported — an off-taxonomy category, a page the Drawing lacks, a box that
grossly overflows its native frame — is reported rather than silently dropped. The importer
never calls a model, so no adapter seam is involved.
"""

import pytest
from sqlmodel import select

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.services.counting_ground_truth import CountingGroundTruthService
from core.services.location_ground_truth import (
    OUT_OF_FRAME,
    UNKNOWN_PAGE,
    UNMAPPED_LABEL,
    LocationGroundTruthService,
)


def _make_drawing_with_pages(session, native_dims: list[tuple[int, int]]) -> Drawing:
    """A Drawing with one Page per native ``(width_pt, height_pt)``, numbered from 1.

    The Page's full-resolution pixel dims are set to a different (larger) frame than its
    native point dims, so a test that passes when the importer normalizes by the native
    dims would fail if it wrongly normalized by the pixel dims.
    """
    drawing = Drawing(name="d")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    for page_number, (native_w, native_h) in enumerate(native_dims, start=1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=f"page_{page_number:04d}_downsampled.png",
                width_px=native_w * 4,
                height_px=native_h * 4,
                native_width_pt=float(native_w),
                native_height_pt=float(native_h),
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


def _obj(category: str, page: int, x: int, y: int, width: int, height: int) -> dict:
    return {
        "id": f"{category}-{page}-{x}-{y}",
        "category": category,
        "page": page,
        "bbox": {"x": x, "y": y, "width": width, "height": height},
    }


def test_import_creates_normalized_boxes_on_correct_pages(session):
    drawing = _make_drawing_with_pages(session, [(1000, 2000), (500, 400)])
    document = {
        "project_id": "prj1",
        "objects": [
            # page 1: [x, y, w, h] px -> normalized by native (1000, 2000)
            _obj("cabinet", 1, 100, 200, 300, 400),
            # page 2: normalized by native (500, 400)
            _obj("countertop", 2, 50, 40, 100, 80),
        ],
    }

    result = LocationGroundTruthService(session).import_objects(drawing.id, document)

    assert result.created == 2
    assert result.problems == []

    by_page = _boxes_by_page(session, drawing)
    assert len(by_page[1]) == 1 and len(by_page[2]) == 1

    box1 = by_page[1][0]
    assert box1.label == "cabinet"
    assert box1.x_min == pytest.approx(0.1)
    assert box1.y_min == pytest.approx(0.1)
    assert box1.x_max == pytest.approx(0.4)  # (100 + 300) / 1000
    assert box1.y_max == pytest.approx(0.3)  # (200 + 400) / 2000

    box2 = by_page[2][0]
    assert box2.label == "countertop"
    assert box2.x_min == pytest.approx(0.1)  # 50 / 500
    assert box2.y_min == pytest.approx(0.1)  # 40 / 400
    assert box2.x_max == pytest.approx(0.3)  # (50 + 100) / 500
    assert box2.y_max == pytest.approx(0.3)  # (40 + 80) / 400


def test_normalization_uses_native_point_dims_not_pixel_dims(session):
    # The Page's native point dims are authoritative for GT (ADR 0022). The helper sets the
    # pixel dims to 4x the native frame; normalization must still divide by the native dims.
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {"objects": [_obj("cabinet", 1, 100, 100, 200, 200)]}

    LocationGroundTruthService(session).import_objects(drawing.id, document)

    box = _boxes_by_page(session, drawing)[1][0]
    # Divided by the native 1000, not the pixel frame's 4000.
    assert box.x_min == pytest.approx(0.1)
    assert box.x_max == pytest.approx(0.3)


def test_project_id_is_ignored(session):
    # The Drawing the import is launched from is authoritative; the file's project_id, even a
    # mismatched one, is read past.
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {
        "project_id": "some-other-project",
        "objects": [_obj("cabinet", 1, 0, 0, 100, 100)],
    }

    result = LocationGroundTruthService(session).import_objects(drawing.id, document)

    assert result.created == 1
    assert result.problems == []


def test_off_taxonomy_category_is_reported_not_dropped(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {
        "objects": [
            _obj("cabinet", 1, 0, 0, 100, 100),
            _obj("windows", 1, 0, 0, 100, 100),  # not in the singular taxonomy
        ],
    }

    result = LocationGroundTruthService(session).import_objects(drawing.id, document)

    # The valid object still imports; the off-taxonomy one is reported, not dropped silently.
    assert result.created == 1
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.kind == UNMAPPED_LABEL
    assert "windows" in problem.detail


def test_unknown_page_is_reported_not_dropped(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])  # only page 1 exists
    document = {
        "objects": [
            _obj("cabinet", 1, 0, 0, 100, 100),
            _obj("cabinet", 9, 0, 0, 100, 100),  # page 9 does not exist
        ],
    }

    result = LocationGroundTruthService(session).import_objects(drawing.id, document)

    assert result.created == 1
    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.kind == UNKNOWN_PAGE
    assert "9" in problem.detail


def test_gross_overflow_is_reported_out_of_frame_and_skipped(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {
        "objects": [
            _obj("cabinet", 1, 0, 0, 100, 100),  # well inside the frame
            _obj("cabinet", 1, 900, 900, 400, 400),  # extends to 1300px on a 1000 frame
        ],
    }

    result = LocationGroundTruthService(session).import_objects(drawing.id, document)

    assert result.created == 1
    assert len(result.problems) == 1
    assert result.problems[0].kind == OUT_OF_FRAME
    # The in-frame box is the only one that landed.
    assert len(_boxes_by_page(session, drawing)[1]) == 1


def test_within_tolerance_overflow_is_clamped_and_accepted(session):
    # A flush-to-edge annotation that spills over by <= ~0.5% is accepted and clamped to the
    # unit square, not rejected as an error.
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    # x_max = 1004 / 1000 = 1.004 -> within 0.5% tolerance -> clamp to 1.0.
    document = {"objects": [_obj("cabinet", 1, 0, 0, 1004, 1004)]}

    result = LocationGroundTruthService(session).import_objects(drawing.id, document)

    assert result.created == 1
    assert result.problems == []
    box = _boxes_by_page(session, drawing)[1][0]
    assert box.x_max == pytest.approx(1.0)
    assert box.y_max == pytest.approx(1.0)
    assert box.x_min == pytest.approx(0.0)


def test_each_distinct_problem_is_reported_once(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {
        "objects": [
            _obj("windows", 1, 0, 0, 100, 100),  # unmapped, twice
            _obj("windows", 1, 10, 10, 100, 100),
            _obj("cabinet", 9, 0, 0, 100, 100),  # unknown page, twice
            _obj("cabinet", 9, 10, 10, 100, 100),
        ],
    }

    result = LocationGroundTruthService(session).import_objects(drawing.id, document)

    assert result.created == 0
    kinds = sorted(problem.kind for problem in result.problems)
    # One report per distinct cause, not one per object.
    assert kinds == [UNKNOWN_PAGE, UNMAPPED_LABEL]


def test_reimport_replaces_rather_than_duplicates(session):
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {"objects": [_obj("cabinet", 1, 0, 0, 100, 100)]}
    service = LocationGroundTruthService(session)

    service.import_objects(drawing.id, document)
    service.import_objects(drawing.id, document)

    assert len(_boxes_by_page(session, drawing)[1]) == 1


# --- opt-in counting-GT derivation (ticket 04, ADR 0025) ------------------------------


def test_derive_counting_sets_totals_from_box_tallies(session):
    # With the opt-in flag set, the import tallies accepted boxes per label per Drawing and
    # writes the counting GT through the counting service — the total for each covered label
    # equals its box tally. A label with no accepted box is left untouched (not zeroed), so
    # the derivation never fabricates a "zero of this object" the boxes did not state.
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {
        "objects": [
            _obj("cabinet", 1, 0, 0, 100, 100),
            _obj("cabinet", 1, 200, 200, 100, 100),
            _obj("countertop", 1, 400, 400, 100, 100),
        ],
    }

    result = LocationGroundTruthService(session).import_objects(
        drawing.id, document, derive_counting=True
    )

    assert result.created == 3
    # Only the covered labels are written — elevation / elevation_callout stay unentered.
    totals = CountingGroundTruthService(session).get_totals(drawing.id)
    assert totals == {"cabinet": 2, "countertop": 1}


def test_derive_counting_leaves_uncovered_labels_untouched(session):
    # A label the boxes do not cover is not asserted as zero — a pre-existing total for it
    # survives the derive, since an objects file need not localize every object type (ADR
    # 0025: counting may legitimately include objects that were not localized).
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    CountingGroundTruthService(session).save(drawing.id, {"elevation": 5})
    document = {"objects": [_obj("cabinet", 1, 0, 0, 100, 100)]}

    LocationGroundTruthService(session).import_objects(
        drawing.id, document, derive_counting=True
    )

    totals = CountingGroundTruthService(session).get_totals(drawing.id)
    # cabinet derived from the box; elevation preserved rather than zeroed.
    assert totals == {"cabinet": 1, "elevation": 5}


def test_derive_counting_off_leaves_existing_counting_untouched(session):
    # The flag is off by default; a location import then touches only LocationGroundTruth and
    # any existing counting total is preserved (ADR 0025 default-off).
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    CountingGroundTruthService(session).save(drawing.id, {"cabinet": 9, "elevation": 3})
    document = {"objects": [_obj("cabinet", 1, 0, 0, 100, 100)]}

    LocationGroundTruthService(session).import_objects(drawing.id, document)

    # Unchanged: neither zeroed for the imported label nor cleared for the untouched one.
    assert CountingGroundTruthService(session).get_totals(drawing.id) == {
        "cabinet": 9,
        "elevation": 3,
    }


def test_derive_counting_tallies_only_accepted_boxes(session):
    # Only boxes that actually landed count toward the totals — an out-of-frame box that was
    # reported and skipped does not inflate the derived total.
    drawing = _make_drawing_with_pages(session, [(1000, 1000)])
    document = {
        "objects": [
            _obj("cabinet", 1, 0, 0, 100, 100),  # accepted
            _obj("cabinet", 1, 900, 900, 400, 400),  # out_of_frame, skipped
            _obj("windows", 1, 0, 0, 100, 100),  # unmapped, skipped
        ],
    }

    result = LocationGroundTruthService(session).import_objects(
        drawing.id, document, derive_counting=True
    )

    assert result.created == 1
    assert CountingGroundTruthService(session).get_totals(drawing.id)["cabinet"] == 1
