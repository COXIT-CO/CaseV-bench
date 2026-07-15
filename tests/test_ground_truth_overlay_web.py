"""GT-only overlay from a Drawing in Library (ticket 12). Two seams:

- ``render_ground_truth_overlay`` (pure): the LocationGroundTruth boxes drawn on the page
  image by themselves, returned as PNG bytes — the shared box-drawing reused from the
  compare overlay (ticket 12), now a single ground-truth group.
- the Drawing detail: a Page that carries ground truth exposes a GT-only overlay
  (``GET /api/drawings/{id}/pages/{n}/ground-truth-overlay``), linked from
  ``GET /api/drawings/{id}`` only where there is GT to draw (``ground_truth_overlay_url``)
  — inspectable independent of any Run.

Ground truth is seeded directly so the boxes are exact without depending on a live import.
"""

from io import BytesIO

from PIL import Image
from sqlmodel import Session

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.results import LabeledBox
from core.utils import render_ground_truth_overlay

CAB = "cabinets"


def test_render_ground_truth_overlay_returns_a_page_sized_png(tmp_path):
    # A page image with two GT boxes → a decodable PNG the same size as the source page.
    image_path = tmp_path / "page.png"
    Image.new("RGB", (120, 80), "white").save(image_path)

    png = render_ground_truth_overlay(
        image_path,
        [LabeledBox(CAB, 0.1, 0.1, 0.4, 0.4), LabeledBox(CAB, 0.5, 0.5, 0.7, 0.7)],
    )

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(png)) as rendered:
        assert rendered.size == (120, 80)


def _seed_drawing(engine, tmp_path, *, with_gt=True) -> int:
    """A one-page Drawing, optionally carrying a location GT box. Returns the Drawing id."""
    with Session(engine) as session:
        drawing = Drawing(name="sample")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)

        image_path = tmp_path / "page_1.png"
        Image.new("RGB", (100, 100), "white").save(image_path)
        page = Page(
            drawing_id=drawing.id,
            page_number=1,
            image_path=str(image_path),
            width_px=100,
            height_px=100,
        )
        session.add(page)
        session.commit()
        session.refresh(page)

        if with_gt:
            session.add(
                LocationGroundTruth(
                    page_id=page.id,
                    label=CAB,
                    x_min=0.1,
                    y_min=0.1,
                    x_max=0.5,
                    y_max=0.5,
                )
            )
            session.commit()
        return drawing.id


def test_detail_links_the_overlay_on_a_page_with_ground_truth(client, engine, tmp_path):
    drawing_id = _seed_drawing(engine, tmp_path, with_gt=True)

    page = client.get(f"/api/drawings/{drawing_id}").json()["pages"][0]

    assert (
        page["ground_truth_overlay_url"]
        == f"/api/drawings/{drawing_id}/pages/1/ground-truth-overlay"
    )


def test_detail_omits_the_overlay_link_when_a_page_has_no_ground_truth(
    client, engine, tmp_path
):
    drawing_id = _seed_drawing(engine, tmp_path, with_gt=False)

    page = client.get(f"/api/drawings/{drawing_id}").json()["pages"][0]

    assert page["ground_truth_overlay_url"] is None


def test_ground_truth_overlay_renders_a_png_for_a_page_with_ground_truth(
    client, engine, tmp_path
):
    drawing_id = _seed_drawing(engine, tmp_path, with_gt=True)

    overlay = client.get(f"/api/drawings/{drawing_id}/pages/1/ground-truth-overlay")

    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"
    assert overlay.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_ground_truth_overlay_404s_for_a_page_without_ground_truth(
    client, engine, tmp_path
):
    # Nothing to draw — the detail never links this, so a direct hit is a 404.
    drawing_id = _seed_drawing(engine, tmp_path, with_gt=False)

    overlay = client.get(f"/api/drawings/{drawing_id}/pages/1/ground-truth-overlay")
    assert overlay.status_code == 404


def test_ground_truth_overlay_unknown_page_is_404(client, engine, tmp_path):
    drawing_id = _seed_drawing(engine, tmp_path, with_gt=True)

    overlay = client.get(f"/api/drawings/{drawing_id}/pages/99/ground-truth-overlay")
    assert overlay.status_code == 404
