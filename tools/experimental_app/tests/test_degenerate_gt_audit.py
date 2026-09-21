"""Repo-wide audit for degenerate stored LocationGroundTruth (adoption ticket 01, ADR 0031).

The importer now refuses a box enclosing no area, but rows written *before* that guard existed
are still in the store. After the port to ``location-scorer`` such a row raises at score time
instead of silently depressing recall, so the store has to be checked before the port lands —
otherwise the port's parity run fails and reads as the port having broken something when in
fact it found a pre-existing data defect.

The findings are built by reading rows directly, not by importing: the importer can no longer
produce one, which is the whole point of the guard.
"""

from conftest import make_drawing_with_pages

from core.audit import audit_degenerate_location_gt
from core.models.drawing import Page
from core.models.location_ground_truth import LocationGroundTruth
from core.services.location_ground_truth import LocationGroundTruthService


def _drawing_with_page(session, name: str) -> Page:
    """A named single-page Drawing — the audit reports per Drawing, so each needs its own
    name to prove a finding is attributed to the right one."""
    return make_drawing_with_pages(session, [(1000, 1000)], name=name).pages[0]


def _box(page: Page, label: str, x_min, y_min, x_max, y_max) -> LocationGroundTruth:
    return LocationGroundTruth(
        page_id=page.id,
        label=label,
        x_min=x_min,
        y_min=y_min,
        x_max=x_max,
        y_max=y_max,
    )


def test_clean_store_reports_no_findings(session):
    # "None found" is the result that unblocks the port cleanly, so it must be an empty list
    # rather than an absence of output.
    page = _drawing_with_page(session, "clean")
    session.add(_box(page, "cabinet", 0.1, 0.1, 0.4, 0.3))
    session.commit()

    assert LocationGroundTruthService(session).find_degenerate_boxes() == []


def test_audit_finds_zero_area_and_inverted_boxes_with_their_drawing(session):
    page = _drawing_with_page(session, "defective")
    session.add(_box(page, "cabinet", 0.2, 0.1, 0.2, 0.3))  # zero width
    session.add(_box(page, "countertop", 0.1, 0.5, 0.4, 0.5))  # zero height
    session.add(_box(page, "elevation", 0.7, 0.1, 0.3, 0.4))  # inverted on x
    session.add(_box(page, "cabinet", 0.1, 0.1, 0.4, 0.4))  # well-formed
    session.commit()

    findings = LocationGroundTruthService(session).find_degenerate_boxes()

    assert len(findings) == 3
    assert {finding.label for finding in findings} == {
        "cabinet",
        "countertop",
        "elevation",
    }
    # Each finding locates the box well enough to fix it at source.
    for finding in findings:
        assert finding.drawing_name == "defective"
        assert finding.page_number == 1
        assert finding.box_id is not None
    reasons = sorted(finding.reason for finding in findings)
    assert reasons == [
        "inverted x coordinates (negative width)",
        "zero height",
        "zero width",
    ]


def test_runner_returns_zero_and_says_so_on_a_clean_store(session, capsys):
    # The count is what ``main`` maps to the process exit code, so a clean store must exit 0
    # — ticket 02 gates the port on that.
    page = _drawing_with_page(session, "clean")
    session.add(_box(page, "cabinet", 0.1, 0.1, 0.4, 0.3))
    session.commit()

    assert audit_degenerate_location_gt(session) == 0
    assert "none found" in capsys.readouterr().out


def test_runner_returns_the_count_and_names_each_box(session, capsys):
    page = _drawing_with_page(session, "defective")
    session.add(_box(page, "cabinet", 0.2, 0.1, 0.2, 0.3))  # zero width
    session.commit()

    assert audit_degenerate_location_gt(session) == 1
    out = capsys.readouterr().out
    # Enough to find the box in the source file it came from.
    assert "defective" in out and "cabinet" in out and "zero width" in out


def test_audit_spans_every_drawing(session):
    # The question the ticket asks is "does *any* already-imported Drawing hold one", so the
    # audit is repo-wide rather than per-Drawing.
    first = _drawing_with_page(session, "first")
    second = _drawing_with_page(session, "second")
    session.add(_box(first, "cabinet", 0.1, 0.1, 0.4, 0.4))  # well-formed
    session.add(_box(second, "cabinet", 0.2, 0.2, 0.2, 0.5))  # zero width
    session.commit()

    findings = LocationGroundTruthService(session).find_degenerate_boxes()

    assert [finding.drawing_name for finding in findings] == ["second"]
