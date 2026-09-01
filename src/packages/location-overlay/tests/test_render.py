"""Tests for the library's entire public surface: ``render()``.

Everything below it — the pixel scaling, the palette, the label placement — is private and
is covered here through the pixels it produces. Nothing names an internal helper or asserts
the shape of an intermediate, so these tests describe *what the picture looks like* and stay
valid through any refactor of the internals. A colour is never imported, only sampled off a
rendered image, which is also the only thing a consumer can do.

Test pages are white and 400x400 unless a test needs otherwise, so a box at 0.25–0.75 lands
on pixels 100–300 and every expected coordinate is arithmetically obvious. Geometry tests
pass ``labels=False``: a label chip is drawn near the box it belongs to, and would otherwise
be the thing under half of these assertions.
"""

import os
import subprocess
import sys

import pytest
from PIL import Image

from location_overlay import render

CABINET = "cabinet"
COUNTERTOP = "countertop"
WHITE = (255, 255, 255)

# The types this project actually detects, kept here because the pair that motivated
# collision handling is in it: `callout` and `floor plan` prefer the same palette entry.
PROJECT_TYPES = ("cabinet", "countertop", "elevation", "callout", "floor plan")


def page(size=(400, 400), mode="RGB", color="white"):
    return Image.new(mode, size, color)


def box(x_min, y_min, x_max, y_max, object_type=CABINET, **extra):
    return {"object_type": object_type, "bbox": [x_min, y_min, x_max, y_max], **extra}


def quarter(object_type=CABINET, **extra):
    """The box every geometry test uses: pixels 100–300 on a 400x400 page."""
    return box(0.25, 0.25, 0.75, 0.75, object_type=object_type, **extra)


def color_of_quarter(image):
    """The colour `quarter()` came out in, read off its top-left corner."""
    return image.getpixel((100, 100))


def contrast_with_white(color):
    """WCAG 2.x contrast ratio between `color` and white."""

    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = color
    luminance = 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
    return 1.05 / (luminance + 0.05)


def thickness_below(image, x, y, color):
    """How many pixels of `color` run downward from (x, y) — a stroke's width."""
    run = 0
    while y + run < image.height and image.getpixel((x, y + run)) == color:
        run += 1
    return run


def stacked(object_types, page_size=(400, 400)):
    """One narrow box per type, side by side, with the point each was drawn at."""
    boxes, points = [], []
    for index, object_type in enumerate(object_types):
        x_min = index / len(object_types)
        boxes.append(box(x_min, 0.4, x_min + 0.5 / len(object_types), 0.9, object_type))
        points.append((round(x_min * page_size[0]), round(0.4 * page_size[1])))
    return boxes, points


# --- Where the box lands ------------------------------------------------------------


def test_a_box_is_outlined_at_its_scaled_coordinates():
    result = render(page(), [quarter()], labels=False, line_width=1)

    color = color_of_quarter(result)
    assert color != WHITE
    assert result.getpixel((300, 100)) == color  # top-right corner
    assert result.getpixel((100, 300)) == color  # bottom-left corner
    assert result.getpixel((300, 300)) == color  # bottom-right corner
    assert result.getpixel((200, 100)) == color  # midpoint of the top edge


def test_the_box_is_outlined_and_never_filled():
    # The drawing under the box is the reason anyone is looking at the page.
    result = render(page(), [quarter()], labels=False, line_width=1)

    assert result.getpixel((200, 200)) == WHITE


def test_pixels_outside_the_box_are_left_alone():
    result = render(page(), [quarter()], labels=False, line_width=1)

    assert result.getpixel((50, 50)) == WHITE
    assert result.getpixel((350, 350)) == WHITE


def test_a_non_square_page_scales_each_axis_by_its_own_side():
    # A square box on a wide page is a wide box: 0.5 of 800 is 400, 0.5 of 200 is 100.
    result = render(
        page(size=(800, 200)), [box(0.25, 0.25, 0.5, 0.5)], labels=False, line_width=1
    )

    color = result.getpixel((200, 50))
    assert color != WHITE
    assert result.getpixel((400, 100)) == color


# --- Stroke width -------------------------------------------------------------------


def test_line_width_is_honoured_in_pixels():
    result = render(page(), [quarter()], labels=False, line_width=5)

    assert thickness_below(result, 200, 100, color_of_quarter(result)) == 5


def test_the_default_stroke_scales_with_the_image():
    # One call site renders both a thumbnail and a full sheet; a fixed default cannot serve
    # both, so the default is derived from the image rather than from a constant.
    thumbnail = render(page(size=(200, 200)), [quarter()], labels=False)
    sheet = render(page(size=(3000, 3000)), [quarter()], labels=False)

    thin = thickness_below(thumbnail, 100, 50, thumbnail.getpixel((50, 50)))
    thick = thickness_below(sheet, 1500, 750, sheet.getpixel((750, 750)))
    assert thin == 1
    assert thick > thin


def test_a_stroke_narrower_than_a_pixel_raises():
    with pytest.raises(ValueError):
        render(page(), [quarter()], line_width=0)


# --- Colour -------------------------------------------------------------------------


def test_two_types_on_one_page_are_never_drawn_in_the_same_colour():
    boxes, points = stacked(PROJECT_TYPES)

    result = render(page(), boxes, labels=False, line_width=1)

    colors = [result.getpixel(point) for point in points]
    assert WHITE not in colors
    assert len(set(colors)) == len(PROJECT_TYPES)


def test_a_type_keeps_its_colour_when_the_page_gains_another_type():
    # Preferring a hashed colour is what keeps one type recognisable from page to page.
    alone = render(page(), [quarter()], labels=False, line_width=1)
    crowded = render(
        page(),
        [quarter(), box(0.05, 0.05, 0.15, 0.15, object_type=COUNTERTOP)],
        labels=False,
        line_width=1,
    )

    assert color_of_quarter(crowded) == color_of_quarter(alone)


def test_a_type_moves_off_its_preferred_colour_only_to_avoid_a_collision():
    # `callout` and `floor plan` prefer the same entry. Sorted order decides: `callout`
    # keeps it, `floor plan` walks to the next free one.
    callout_alone = render(page(), [quarter("callout")], labels=False, line_width=1)
    plan_alone = render(page(), [quarter("floor plan")], labels=False, line_width=1)
    both, points = stacked(("callout", "floor plan"))
    together = render(page(), both, labels=False, line_width=1)

    assert color_of_quarter(callout_alone) == color_of_quarter(
        plan_alone
    )  # the collision
    callout, plan = (together.getpixel(point) for point in points)
    assert callout == color_of_quarter(callout_alone)
    assert plan != callout


def test_which_type_moves_does_not_depend_on_the_order_the_boxes_arrived_in():
    boxes, points = stacked(PROJECT_TYPES)
    reordered = list(reversed(boxes))

    forward = render(page(), boxes, labels=False, line_width=1)
    backward = render(page(), reordered, labels=False, line_width=1)

    assert [forward.getpixel(point) for point in points] == [
        backward.getpixel(point) for point in points
    ]


def test_the_colour_a_type_gets_is_pinned():
    # Nothing about the picture is more visible than this, and a palette edit that moves it
    # should be a deliberate change to this line rather than a side effect of another one.
    result = render(page(), [quarter()], labels=False, line_width=1)

    assert color_of_quarter(result) == (205, 25, 55)


def test_the_same_type_is_the_same_colour_at_any_image_size():
    small = render(page(), [quarter()], labels=False, line_width=1)
    large = render(page(size=(900, 900)), [quarter()], labels=False, line_width=1)

    assert large.getpixel((225, 225)) == color_of_quarter(small)


def test_colours_survive_a_change_of_process():
    # `hash()` is salted per process: used here, a type would change colour on restart and
    # nothing in a single-process test would notice.
    program = (
        "from PIL import Image; from location_overlay import render;"
        " page = Image.new('RGB', (400, 400), 'white');"
        " boxes = [{'object_type': 'cabinet', 'bbox': [0.25, 0.25, 0.75, 0.75]}];"
        " print(render(page, boxes, labels=False, line_width=1).getpixel((100, 100)))"
    )
    seen = {
        seed: subprocess.run(
            [sys.executable, "-c", program],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("0", "1")
    }

    expected = render(page(), [quarter()], labels=False, line_width=1).getpixel(
        (100, 100)
    )
    assert set(seen.values()) == {str(expected)}


def test_every_colour_the_library_can_draw_carries_white_label_text():
    # Sixteen types exhaust the palette, so this covers every colour a caller can be given.
    types = tuple(f"type-{index}" for index in range(16))
    boxes, points = stacked(types, page_size=(1600, 400))

    result = render(page(size=(1600, 400)), boxes, labels=False, line_width=1)

    colors = [result.getpixel(point) for point in points]
    assert len(set(colors)) == 16
    for color in colors:
        assert contrast_with_white(color) >= 4.5, color


def test_more_types_than_colours_still_renders():
    types = tuple(f"type-{index}" for index in range(40))
    boxes, points = stacked(types, page_size=(4000, 400))

    result = render(page(size=(4000, 400)), boxes, labels=False, line_width=1)

    colors = [result.getpixel(point) for point in points]
    assert WHITE not in colors
    assert len(set(colors)) == 16  # past sixteen, colours repeat


# --- Labels -------------------------------------------------------------------------


def test_a_label_is_drawn_above_the_box_by_default():
    labelled = render(page(), [quarter()], line_width=1)
    bare = render(page(), [quarter()], labels=False, line_width=1)

    strip = [(x, y) for x in range(100, 300) for y in range(80, 100)]
    assert any(labelled.getpixel(point) != WHITE for point in strip)
    assert all(bare.getpixel(point) == WHITE for point in strip)


def test_the_label_sits_on_a_filled_chip_in_the_box_colour():
    # Bare text over dense drawing linework is unreadable exactly where the objects are.
    result = render(page(), [quarter()], line_width=1)

    assert result.getpixel((101, 99)) == color_of_quarter(result)


def test_a_label_with_no_room_above_the_box_drops_inside_it():
    result = render(page(), [box(0.0, 0.0, 0.5, 0.5)], line_width=1)

    assert result.getpixel((2, 2)) == result.getpixel((0, 0))
    assert result.getpixel((2, 2)) != WHITE


def test_a_label_at_the_right_edge_stays_on_the_page():
    result = render(page(), [box(0.9, 0.5, 0.99, 0.6)], line_width=1)

    assert result.getpixel((399, 198)) == result.getpixel((360, 200))
    assert result.getpixel((399, 198)) != WHITE


def test_labels_are_drawn_after_every_outline():
    # The second box's left edge runs straight through the first box's chip. Drawn box by
    # box, it would slice the label in half; the label pass runs last, so it does not.
    result = render(
        page(),
        [
            box(0.5, 0.5, 0.9, 0.9),  # its chip runs right from x=200, just above y=200
            box(0.5, 0.0, 0.53, 1.0, object_type=COUNTERTOP),  # left edge down x=200
        ],
        line_width=3,
    )

    first_box_color = result.getpixel((300, 201))  # its own top edge, clear of the chip
    assert first_box_color != WHITE
    assert result.getpixel((201, 198)) == first_box_color


def test_labels_off_leaves_nothing_but_outlines():
    result = render(page(), [quarter()], labels=False, line_width=1)

    painted = {
        (x, y)
        for x in range(400)
        for y in range(400)
        if result.getpixel((x, y)) != WHITE
    }
    assert painted == {
        (x, y)
        for x in range(100, 301)
        for y in range(100, 301)
        if x in (100, 300) or y in (100, 300)
    }


# --- Pages --------------------------------------------------------------------------


def test_boxes_from_more_than_one_page_raise_and_name_the_pages():
    # Page 2's boxes over page 1 make a plausible-looking picture that is simply wrong.
    with pytest.raises(ValueError) as raised:
        render(page(), [quarter(page=1), quarter(page=2)])

    assert "[1, 2]" in str(raised.value)


def test_a_page_number_is_never_used_to_filter():
    # The caller renders the page and passes that page's boxes; the library draws them.
    result = render(page(), [quarter(page=7)], labels=False, line_width=1)

    assert color_of_quarter(result) != WHITE


def test_boxes_without_a_page_are_drawn():
    result = render(page(), [quarter()], labels=False, line_width=1)

    assert color_of_quarter(result) != WHITE


def test_a_page_number_may_be_present_on_some_boxes_and_absent_on_others():
    result = render(page(), [quarter(page=1), quarter()], labels=False, line_width=1)

    assert color_of_quarter(result) != WHITE


# --- Malformed shape: raises --------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param({"bbox": [0.1, 0.1, 0.2, 0.2]}, id="no-object-type"),
        pytest.param(
            {"object_type": 7, "bbox": [0.1, 0.1, 0.2, 0.2]}, id="type-not-a-string"
        ),
        pytest.param({"object_type": CABINET}, id="no-bbox"),
        pytest.param(
            {"object_type": CABINET, "bbox": [0.1, 0.1, 0.2]}, id="three-numbers"
        ),
        pytest.param(
            {"object_type": CABINET, "bbox": [0.1, 0.1, 0.2, 0.2, 0.3]},
            id="five-numbers",
        ),
        pytest.param(
            {"object_type": CABINET, "bbox": "0.1,0.1,0.2,0.2"}, id="bbox-a-string"
        ),
        pytest.param(
            {"object_type": CABINET, "bbox": [0.1, 0.1, 0.2, None]},
            id="bbox-holds-none",
        ),
        pytest.param(
            {"object_type": CABINET, "bbox": [0.1, 0.1, 0.2, True]},
            id="bbox-holds-a-bool",
        ),
        pytest.param(
            {"object_type": CABINET, "bbox": [0.1, 0.1, 0.2, float("nan")]},
            id="bbox-holds-nan",
        ),
        pytest.param(
            {"object_type": CABINET, "bbox": [0.1, 0.1, 0.2, float("inf")]},
            id="bbox-holds-inf",
        ),
    ],
)
def test_a_box_that_cannot_be_drawn_at_all_raises(malformed):
    with pytest.raises(ValueError):
        render(page(), [malformed])


def test_the_error_names_the_offending_index_so_it_can_be_found_in_the_source_data():
    boxes = [quarter(), quarter(), {"object_type": CABINET, "bbox": [0.1, 0.2]}]

    with pytest.raises(ValueError) as raised:
        render(page(), boxes)

    assert "boxes[2]" in str(raised.value)


def test_nothing_is_drawn_when_a_later_box_is_malformed():
    # Validation runs before the first stroke, so a rejected call never leaves a caller
    # holding a half-drawn page it might still save.
    original = page()

    with pytest.raises(ValueError):
        render(original, [quarter(), {"object_type": CABINET, "bbox": []}])

    assert original.getpixel((100, 100)) == WHITE


# --- Malformed geometry: drawn ------------------------------------------------------


@pytest.mark.parametrize(
    "geometry",
    [
        pytest.param(box(0.75, 0.25, 0.25, 0.75), id="inverted-horizontally"),
        pytest.param(box(0.25, 0.75, 0.75, 0.25), id="inverted-vertically"),
        pytest.param(box(0.5, 0.25, 0.5, 0.75), id="zero-width"),
        pytest.param(box(0.25, 0.5, 0.75, 0.5), id="zero-height"),
        pytest.param(box(0.5, 0.5, 0.5, 0.5), id="zero-area"),
        pytest.param(box(-0.2, -0.2, 0.5, 0.5), id="off-the-top-left"),
        pytest.param(box(0.5, 0.5, 1.4, 1.4), id="past-the-bottom-right"),
    ],
)
def test_a_geometrically_wrong_box_is_drawn_rather_than_rejected(geometry):
    # Looking at exactly these is the point of rendering an overlay; a box quietly dropped
    # for being malformed hides the model's worst output from whoever is reviewing it.
    result = render(page(), [geometry], labels=False, line_width=1)

    assert result.tobytes() != page().tobytes()


def test_an_inverted_box_is_drawn_exactly_where_its_ordered_twin_would_be():
    inverted = render(page(), [box(0.75, 0.75, 0.25, 0.25)], line_width=1)
    ordered = render(page(), [quarter()], line_width=1)

    assert inverted.tobytes() == ordered.tobytes()


def test_a_box_past_the_edge_is_clipped_by_the_canvas_and_not_clamped_to_it():
    # Clamping would draw a border the model never predicted, making a hallucination
    # reaching off the sheet look like a legitimate edge detection.
    result = render(page(), [box(-0.5, -0.5, 0.5, 0.5)], labels=False, line_width=1)

    color = result.getpixel((200, 100))  # the edges that are on the page
    assert color != WHITE
    assert result.getpixel((100, 200)) == color
    # The left and top edges are off the canvas, and no substitute is drawn along it: the
    # only pixels painted on the first row and column are where the *bottom* and *right*
    # edges cross them.
    assert [y for y in range(400) if result.getpixel((0, y)) != WHITE] == [200]
    assert [x for x in range(400) if result.getpixel((x, 0)) != WHITE] == [200]


def test_a_box_entirely_off_the_page_leaves_no_trace():
    result = render(page(), [box(1.5, 1.5, 2.0, 2.0)], labels=False, line_width=1)

    assert result.tobytes() == page().tobytes()


def test_a_box_entirely_off_the_page_leaves_no_trace_with_labels_on_too():
    # Off-page horizontally, on-page vertically: the box itself is invisible (clipped by
    # Pillow), but before the entirely-off-canvas check, `x` alone was clamped back onto the
    # page, leaving a floating chip pinned to the right edge with no box for it to label.
    result = render(page(), [box(1.5, 0.4, 1.6, 0.5)], line_width=1)

    assert result.tobytes() == page().tobytes()


# --- Draw order ---------------------------------------------------------------------


def test_a_later_box_is_drawn_over_an_earlier_one():
    elsewhere = box(0.05, 0.05, 0.15, 0.15, object_type=COUNTERTOP)

    result = render(
        page(),
        [quarter(), quarter(object_type=COUNTERTOP), elsewhere],
        labels=False,
        line_width=1,
    )

    countertop = result.getpixel((20, 20))
    assert countertop != WHITE
    assert color_of_quarter(result) == countertop


# --- The image the caller passed in --------------------------------------------------


def test_the_caller_s_image_is_never_modified():
    original = page()
    before = original.tobytes()

    render(original, [quarter()])

    assert original.tobytes() == before


def test_the_result_is_a_new_image_even_when_nothing_is_drawn():
    original = page()

    result = render(original, [])

    assert result is not original
    assert result.tobytes() == original.tobytes()


@pytest.mark.parametrize("mode", ["1", "L", "P", "RGB", "RGBA"])
def test_any_page_mode_comes_back_as_rgb_carrying_colour(mode):
    # A colour overlay composited onto a grayscale canvas comes out grey.
    result = render(page(mode=mode), [quarter()], labels=False, line_width=1)

    assert result.mode == "RGB"
    assert color_of_quarter(result) == (205, 25, 55)


def test_the_page_keeps_its_size():
    result = render(page(size=(1234, 567)), [quarter()])

    assert result.size == (1234, 567)


# --- Input handling ------------------------------------------------------------------


def test_boxes_may_be_any_sequence():
    result = render(page(), (quarter(),), labels=False, line_width=1)

    assert color_of_quarter(result) != WHITE


def test_the_caller_s_boxes_are_not_mutated():
    boxes = [quarter(page=1)]

    render(page(), boxes)

    assert boxes == [
        {"object_type": CABINET, "bbox": [0.25, 0.25, 0.75, 0.75], "page": 1}
    ]


def test_the_same_inputs_render_the_same_pixels():
    boxes = [quarter(), box(0.1, 0.1, 0.2, 0.2, object_type=COUNTERTOP)]

    first = render(page(), boxes)
    second = render(page(), boxes)

    assert first.tobytes() == second.tobytes()


def test_an_object_type_with_no_name_still_draws_its_box():
    result = render(page(), [quarter(object_type="")], labels=False, line_width=1)

    assert color_of_quarter(result) != WHITE


# --- The example in the README --------------------------------------------------------
#
# The README is what a consumer reads before installing. Its numbers cannot be asserted the
# way a scorer's can — the output is a picture — so what is pinned here is that the snippet
# runs as printed and returns the kind of image the surrounding prose promises.


README_PREDICTIONS = [
    {"object_type": "cabinet", "bbox": [0.10, 0.10, 0.30, 0.30], "page": 1},
    {"object_type": "cabinet", "bbox": [0.50, 0.10, 0.58, 0.30], "page": 1},
    {"object_type": "countertop", "bbox": [0.10, 0.60, 0.90, 0.70], "page": 1},
]


def test_the_readme_example_renders_a_new_rgb_page_of_the_same_size():
    sheet = page(size=(1650, 1275))

    result = render(sheet, README_PREDICTIONS)

    assert result.mode == "RGB"
    assert result.size == sheet.size
    assert result is not sheet
    assert result.tobytes() != sheet.tobytes()


def test_the_readme_filtering_idiom_selects_one_page_of_a_whole_document():
    document = README_PREDICTIONS + [
        {"object_type": "cabinet", "bbox": [0.20, 0.20, 0.40, 0.40], "page": 2}
    ]

    result = render(page(), [box for box in document if box["page"] == 1])

    assert result.mode == "RGB"
