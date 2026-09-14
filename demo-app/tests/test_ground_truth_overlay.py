"""Ground-truth overlay renderer (ticket 05, ADR 0024). Mirrors ``test_overlay_color.py``:
the GT overlay draws a page's LocationGroundTruth boxes through the *same* shared renderer as
the prediction overlay, so each box carries its ObjectType's colour (``GROUND_TRUTH_COLOR`` is
**not** revived — ground truth is coloured by class, not provenance). An empty box set draws
nothing (the plain page, not an error), and an off-taxonomy label degrades to the neutral
fallback rather than raising.
"""

from io import BytesIO
from pathlib import Path

from PIL import Image

from core.models.location_ground_truth import LocationGroundTruth
from core.utils import UNKNOWN_LABEL_COLOR, color_for_label, ground_truth_overlay_png


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _white_page(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 100), "white").save(path)
    return path


def _box(label: str, x_min: float) -> LocationGroundTruth:
    # A stored GT row (no session needed) — the same shape the route feeds the renderer.
    return LocationGroundTruth(
        page_id=1, label=label, x_min=x_min, y_min=0.1, x_max=x_min + 0.3, y_max=0.9
    )


def _open_rgb(png: bytes) -> Image.Image:
    return Image.open(BytesIO(png)).convert("RGB")


def test_gt_overlay_colours_each_box_by_its_label(tmp_path):
    page = _white_page(tmp_path / "page.png")

    # Two GT boxes of different ObjectTypes, placed apart so their outlines don't overlap. On a
    # 100px-wide page the cabinet box's left edge sits at x≈5, the countertop box's at x≈55;
    # both span y 0.1–0.9, so a pixel at (edge, 50) lands on each box's outline.
    png = ground_truth_overlay_png(
        page, [_box("cabinet", 0.05), _box("countertop", 0.55)]
    )

    rgb = _open_rgb(png)
    # Each box's outline carries *its own* label's colour, at its own location — so a bug that
    # swapped the colours (or shared one) fails here, not just a "both colours appear somewhere"
    # check. This is the same palette the prediction overlay uses, proving the shared renderer.
    assert rgb.getpixel((5, 50)) == _rgb(color_for_label("cabinet"))
    assert rgb.getpixel((55, 50)) == _rgb(color_for_label("countertop"))


def test_gt_overlay_empty_set_draws_no_boxes(tmp_path):
    page = _white_page(tmp_path / "page.png")

    png = ground_truth_overlay_png(page, [])

    rgb = _open_rgb(png)
    # A page with no ground truth yields the plain page image — every pixel stays white, no box
    # is drawn, and nothing raises.
    assert rgb.getcolors() == [(100 * 100, (255, 255, 255))]


def test_gt_overlay_off_taxonomy_label_degrades_to_fallback(tmp_path):
    page = _white_page(tmp_path / "page.png")

    # An off-taxonomy label (a stray category the importer would have reported) still draws, in
    # the neutral fallback, rather than raising.
    png = ground_truth_overlay_png(page, [_box("not_a_real_label", 0.05)])

    rgb = _open_rgb(png)
    assert rgb.getpixel((5, 50)) == _rgb(UNKNOWN_LABEL_COLOR)
