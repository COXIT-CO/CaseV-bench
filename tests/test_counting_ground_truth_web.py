"""Web-layer checks for the CountingGroundTruth entry form: the form offers one
field per taxonomy label, saving persists the totals, and an existing entry is
pre-filled for editing (ticket 07)."""

import pytest
from sqlmodel import Session

from services.counting_ground_truth import CountingGroundTruthService
from services.drawing import DrawingService
from services.pdf_processing import PDFProcessingService

GT_URL = "/drawings/{id}/counting-ground-truth"


@pytest.fixture
def drawing_id(engine, sample_pdf, tmp_path) -> int:
    with Session(engine) as session:
        service = DrawingService(
            session,
            pdf_service=PDFProcessingService(dpi=72),
            cache_root=tmp_path / "drawings",
        )
        return service.ingest(sample_pdf, name="sample").id


def test_form_offers_a_field_per_taxonomy_label(client, drawing_id):
    response = client.get(GT_URL.format(id=drawing_id))
    assert response.status_code == 200
    for label in ("cabinets", "countertops", "elevations", "elevation_callout"):
        assert f'name="{label}"' in response.text


def test_saving_persists_totals(client, engine, drawing_id):
    response = client.post(
        GT_URL.format(id=drawing_id),
        data={
            "cabinets": 4,
            "countertops": 2,
            "elevations": 1,
            "elevation_callout": 0,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(engine) as session:
        totals = CountingGroundTruthService(session).get_totals(drawing_id)
    assert totals == {
        "cabinets": 4,
        "countertops": 2,
        "elevations": 1,
        "elevation_callout": 0,
    }


def test_existing_totals_are_prefilled_for_editing(client, drawing_id):
    client.post(
        GT_URL.format(id=drawing_id),
        data={
            "cabinets": 7,
            "countertops": 0,
            "elevations": 0,
            "elevation_callout": 0,
        },
        follow_redirects=False,
    )
    form = client.get(GT_URL.format(id=drawing_id))
    # The cabinets field carries the previously saved 7 as its value.
    assert 'name="cabinets"' in form.text
    assert 'value="7"' in form.text


def test_form_for_unknown_drawing_returns_404(client):
    assert client.get(GT_URL.format(id=999)).status_code == 404
