"""Prediction-overlay colouring by ObjectType (ticket 06, ADR 0021).

``color_for_label`` maps each of the four ObjectTypes to a fixed, distinct colour and
degrades gracefully for an off-taxonomy label. The overlay renderer draws each box in its
own label's colour (replacing the single prediction colour), so a dense page reads by
class; a salvaged/edited overlay inherits the same colouring through ``draw_overlay``.
"""

from pathlib import Path

from PIL import Image

from core.models.results import OBJECT_LABELS, BoundingBox, LocationDetection
from core.utils import (
    LABEL_COLORS,
    UNKNOWN_LABEL_COLOR,
    color_for_label,
    draw_overlay,
)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def test_color_for_label_is_distinct_per_object_type():
    colors = [color_for_label(label) for label in OBJECT_LABELS]
    # Every ObjectType gets its own colour — no two labels collide, so a dense page can be
    # read by class.
    assert len(set(colors)) == len(OBJECT_LABELS)


def test_palette_covers_exactly_the_taxonomy():
    # The palette must key on the same labels the taxonomy defines — no more, no less. A
    # distinct-colours check alone would still pass if a new ObjectType were added to the
    # Literal but forgotten here (it would silently draw in the fallback grey), so pin the
    # keys to OBJECT_LABELS directly.
    assert set(LABEL_COLORS) == set(OBJECT_LABELS)


def test_color_for_label_handles_unknown_label():
    # An off-taxonomy string (e.g. a salvaged box with a stray label) still draws, in the
    # neutral fallback, rather than raising.
    color = color_for_label("not_a_real_label")
    assert color == UNKNOWN_LABEL_COLOR
    assert color not in {color_for_label(label) for label in OBJECT_LABELS}


def _white_page(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 100), "white").save(path)
    return path


def _detection(label: str, x_min: float) -> LocationDetection:
    return LocationDetection(
        label=label,
        bounding_box=BoundingBox(x_min=x_min, y_min=0.1, x_max=x_min + 0.3, y_max=0.9),
    )


def test_draw_overlay_colours_each_box_by_its_label(tmp_path):
    page = _white_page(tmp_path / "page.png")
    dest = tmp_path / "overlay.png"

    # Two boxes of different ObjectTypes, placed apart so their outlines don't overlap. On a
    # 100px-wide page: the cabinets box's left edge sits at x≈5, the countertops box's at
    # x≈55; both span y 0.1–0.9, so a pixel at (edge, 50) lands on each box's outline.
    draw_overlay(
        page,
        [_detection("cabinet", 0.05), _detection("countertop", 0.55)],
        dest,
    )

    with Image.open(dest) as overlay:
        rgb = overlay.convert("RGB")
        # Each box's outline carries *its own* label's colour, at its own location — so a bug
        # that swapped the two colours (or shared one across boxes) would fail here, not just
        # a check that both colours appear somewhere on the page.
        assert rgb.getpixel((5, 50)) == _rgb(color_for_label("cabinet"))
        assert rgb.getpixel((55, 50)) == _rgb(color_for_label("countertop"))
