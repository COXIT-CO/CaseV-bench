"""Service-layer checks for CountingGroundTruth: entered totals persist and read
back per Drawing and label, editing upserts rather than duplicating, drawings are
isolated, and a label outside the fixed taxonomy is rejected (ticket 07)."""

import pytest

from core.models.drawing import Drawing
from core.services.counting_ground_truth import CountingGroundTruthService

FULL_TOTALS = {
    "cabinets": 3,
    "countertops": 1,
    "elevations": 2,
    "elevation_callout": 0,
}


def _make_drawing(session, name: str = "d") -> Drawing:
    drawing = Drawing(name=name)
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    return drawing


def test_saved_totals_read_back_per_label(session):
    drawing = _make_drawing(session)
    service = CountingGroundTruthService(session)

    service.save(drawing.id, FULL_TOTALS)

    assert service.get_totals(drawing.id) == FULL_TOTALS


def test_get_totals_empty_before_any_entry(session):
    drawing = _make_drawing(session)
    assert CountingGroundTruthService(session).get_totals(drawing.id) == {}


def test_saving_again_updates_in_place(session):
    drawing = _make_drawing(session)
    service = CountingGroundTruthService(session)

    service.save(drawing.id, FULL_TOTALS)
    service.save(drawing.id, {**FULL_TOTALS, "cabinets": 9})

    totals = service.get_totals(drawing.id)
    assert totals["cabinets"] == 9
    # One row per label — the edit updated, it did not append a duplicate.
    assert len(totals) == len(FULL_TOTALS)


def test_totals_are_isolated_per_drawing(session):
    first = _make_drawing(session, "first")
    second = _make_drawing(session, "second")
    service = CountingGroundTruthService(session)

    service.save(first.id, {**FULL_TOTALS, "cabinets": 5})
    service.save(second.id, {**FULL_TOTALS, "cabinets": 2})

    assert service.get_totals(first.id)["cabinets"] == 5
    assert service.get_totals(second.id)["cabinets"] == 2


def test_label_outside_taxonomy_is_rejected(session):
    drawing = _make_drawing(session)
    with pytest.raises(ValueError):
        CountingGroundTruthService(session).save(drawing.id, {"windows": 1})
