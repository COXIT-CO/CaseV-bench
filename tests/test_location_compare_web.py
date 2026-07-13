"""GT-vs-prediction overlay drill-down (ticket 12). Two seams:

- ``render_compare_overlay`` (pure): predicted + GT boxes drawn together on the page
  image, returned as PNG bytes — the shared box-drawing reused from the prediction
  overlay (ticket 09), now two color-coded groups.
- the Result drill-down: a scored location Result renders a per-page compare overlay
  alongside the raw predicted JSON, degrading gracefully on a page with no GT.

Results are seeded directly so the boxes are exact (one matched, one spurious prediction,
one missed GT box — a mixed-match Result) without depending on a live model.
"""

from io import BytesIO

from PIL import Image
from sqlmodel import Session

from models.drawing import Drawing, Page
from models.location_ground_truth import LocationGroundTruth
from models.prompt import Prompt, Task
from models.results import BoundingBox, LabeledBox, LocationDetection, LocationResult
from models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from utils import render_compare_overlay

CAB = "cabinets"


def _detection(x_min, y_min, x_max, y_max, label=CAB) -> LocationDetection:
    return LocationDetection(
        label=label,
        bounding_box=BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
    )


def test_render_compare_overlay_returns_a_page_sized_png(tmp_path):
    # A page image with one predicted box and one GT box → a decodable PNG the same
    # size as the source page, with both groups drawn.
    image_path = tmp_path / "page.png"
    Image.new("RGB", (120, 80), "white").save(image_path)

    png = render_compare_overlay(
        image_path,
        [_detection(0.1, 0.1, 0.4, 0.4)],
        [LabeledBox(CAB, 0.5, 0.5, 0.7, 0.7)],
    )

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(png)) as rendered:
        assert rendered.size == (120, 80)


def _seed_mixed_match_result(engine, tmp_path, *, with_gt=True) -> int:
    """A location Result with a matched box, a spurious prediction, and (optionally) a
    missed GT box. Returns the Result id."""
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
            # One GT box the prediction matches, one it never covers (a false negative).
            session.add(
                LocationGroundTruth(
                    page_id=page.id,
                    label=CAB,
                    x_min=0.0,
                    y_min=0.0,
                    x_max=0.5,
                    y_max=0.5,
                )
            )
            session.add(
                LocationGroundTruth(
                    page_id=page.id,
                    label=CAB,
                    x_min=0.8,
                    y_min=0.8,
                    x_max=1.0,
                    y_max=1.0,
                )
            )

        prompt = Prompt(
            task=Task.location, family="boxes", version=1, text="find boxes"
        )
        session.add(prompt)
        session.commit()
        session.refresh(prompt)

        run = Run(
            task=Task.location,
            prompt_id=prompt.id,
            drawing_id=drawing.id,
            status=RunStatus.done,
            progress=1,
            total_units=1,
            dpi=200,
            downsample_px=1568,
            max_tokens=1024,
            prefill=True,
            temperature=0.0,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        result = Result(run_id=run.id, model="anthropic/claude-sonnet-4.5")
        session.add(result)
        session.commit()
        session.refresh(result)

        parsed = LocationResult(
            detections=[
                _detection(
                    0.0, 0.0, 0.5, 0.5
                ),  # matches the first GT box (a true positive)
                _detection(0.6, 0.0, 0.7, 0.1),  # overlaps no GT box (a false positive)
            ]
        )
        session.add(
            Prediction(
                result_id=result.id,
                page_id=page.id,
                page_number=1,
                status=PredictionStatus.ok,
                raw_content=parsed.model_dump_json(),
                parsed_json=parsed.model_dump_json(),
            )
        )
        session.commit()
        return result.id


def test_drilldown_renders_compare_view_for_mixed_match_result(
    client, engine, tmp_path
):
    result_id = _seed_mixed_match_result(engine, tmp_path, with_gt=True)

    page = client.get(f"/results/{result_id}")
    assert page.status_code == 200
    # The compare section and its per-page overlay + raw predicted JSON are present.
    assert "Ground truth vs prediction" in page.text
    compare_url = f"/results/{result_id}/pages/1/compare-overlay"
    assert compare_url in page.text
    assert "cabinets" in page.text  # the predicted JSON alongside the overlay

    overlay = client.get(compare_url)
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"


def test_drilldown_handles_page_with_no_ground_truth(client, engine, tmp_path):
    # No GT imported: the compare overlay still renders (predictions only) and the page
    # is flagged as having no ground truth rather than 500ing.
    result_id = _seed_mixed_match_result(engine, tmp_path, with_gt=False)

    page = client.get(f"/results/{result_id}")
    assert page.status_code == 200
    assert "no ground truth" in page.text

    overlay = client.get(f"/results/{result_id}/pages/1/compare-overlay")
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"


def test_compare_overlay_missing_prediction_404s(client):
    missing = client.get("/results/999/pages/1/compare-overlay")
    assert missing.status_code == 404
