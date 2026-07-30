"""Bridge between this app's own data and location_scorer.score()
(packages/location-scorer in the monorepo root -- a pure, dependency-free
IoU-matching scorer with no notion of sessions, pages, or this app's
schemas; it only knows {"object_type", "bbox": [x_min,y_min,x_max,y_max],
"page"} dicts sharing one coordinate space).

Both sides of the comparison need converting into that shape:

- Predictions: this app's own `PageResult.parsed.objects` (0-1000
  normalized, see DetectedObject) -- rescaled here to PIXEL space using
  each page's stored width/height (the true render resolution for
  whatever DPI this session was rendered at -- see PageRecord.width/height
  in session_store.py), the same rescale parser.build_location_export
  already does for the benchmark export file. Deliberately NOT reusing
  build_location_export/ExportLocationObject's output directly: that
  export's `category` is the PLURAL form (LABEL_TO_CATEGORY -- "cabinets",
  "elevations", ...), whereas a benchmark ground-truth file's own
  `category` field uses the SINGULAR form ("cabinet", "elevation", ...).
  Using the raw singular `label` here keeps both sides speaking the same
  vocabulary without a translation step in either direction. This app's
  own renders (and therefore its own predictions) are already in whatever
  space `page.get_pixmap` produces, which PyMuPDF derives directly from
  the PDF page's own `/Rotate` attribute -- i.e. already rotation-aware.

- Ground truth: an uploaded ExportLocationResponse-shaped payload. A
  benchmark project's own prj*-obj-location.json stores its box
  (`{x, y, width, height}`) in PDF point space that already reflects
  the page's own `/Rotate` -- i.e. the same space PyMuPDF's
  `page.get_pixmap()` renders into (and therefore the same space this
  app's own predictions are already in), just not yet scaled to this
  session's render DPI. Verified visually and precisely: scaling the raw
  points by nothing but this session's own DPI/72 zoom and drawing them
  directly on the actual rendered page (matching pdf_processor.py exactly,
  rotation included) lands every box, down to individual callout-symbol
  circles, exactly on the real content -- no rotation-matrix step needed
  at all. (An earlier version of this function applied PyMuPDF's
  `derotation_matrix` on top of the DPI scale, on the mistaken assumption
  that ground truth was authored in the raw, UNROTATED MediaBox frame --
  a plausible-looking but wrong reading of one earlier, less careful
  visual check. That extra transform double-rotated an already
  rotation-consistent point and was removed once a precise crop-level
  re-check showed the plain scale alone was exact.)
"""
from __future__ import annotations

from location_scorer import score as location_scorer_score

from app.models.schemas import ExportLocationResponse, PageResult


def predictions_to_boxes(
    results: list[PageResult], page_dimensions: dict[int, tuple[int, int]]
) -> list[dict]:
    boxes: list[dict] = []
    for result in results:
        if result.parsed is None:
            continue
        width, height = page_dimensions.get(result.page_number, (1, 1))
        for obj in result.parsed.objects:
            boxes.append(
                {
                    "object_type": obj.label,
                    "bbox": [
                        (obj.x_min / 1000) * width,
                        (obj.y_min / 1000) * height,
                        (obj.x_max / 1000) * width,
                        (obj.y_max / 1000) * height,
                    ],
                    "page": result.page_number,
                }
            )
    return boxes


def ground_truth_to_boxes(
    ground_truth: ExportLocationResponse,
    dpi: int,
    space: str = "pdf_points",
) -> list[dict]:
    """Project ground-truth boxes into this session's actual render pixel
    space. `space` says what coordinate space `ground_truth` is already in
    (see ScoreRequest.ground_truth_space's docstring):

    - "pdf_points" (a benchmark project's own prj*-obj-location.json): PDF
      point space that already reflects the page's own rotation -- just
      needs scaling by this session's DPI/72 zoom. See module docstring
      for how this was verified.
    - "render_pixels" (this app's own GET .../export/locations output):
      already in the target pixel space -- passed through unchanged.
    """
    zoom = dpi / 72.0 if space == "pdf_points" else 1.0
    boxes: list[dict] = []
    for obj in ground_truth.objects:
        boxes.append(
            {
                "object_type": obj.category,
                "bbox": [
                    obj.bbox.x * zoom,
                    obj.bbox.y * zoom,
                    (obj.bbox.x + obj.bbox.width) * zoom,
                    (obj.bbox.y + obj.bbox.height) * zoom,
                ],
                "page": obj.page,
            }
        )
    return boxes


def score_session(
    results: list[PageResult],
    page_dimensions: dict[int, tuple[int, int]],
    ground_truth: ExportLocationResponse,
    iou_threshold: float,
    dpi: int,
    ground_truth_space: str = "pdf_points",
    include_objects: bool = False,
) -> dict:
    predictions = predictions_to_boxes(results, page_dimensions)
    truth = ground_truth_to_boxes(ground_truth, dpi, space=ground_truth_space)
    return location_scorer_score(
        predictions, truth, iou_threshold=iou_threshold, include_objects=include_objects
    )
