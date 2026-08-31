"""Tests for the library's entire public surface: ``location_parser.parse()``.

Everything below ``parse()`` -- extraction, sanitization, truncation repair, box
validation -- is private and is covered here through its effect on the returned
result. Nothing in these tests names an internal helper, so the tests describe
*parsing behaviour* and stay valid through any refactor of the internals.
"""

import json

import pytest

from location_parser import parse

# --- Clean input --------------------------------------------------------------------


def test_clean_json_parses_completely():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0.1, 0.1, 0.3, 0.3]}]})

    result = parse(text)

    assert result["boxes"] == [
        {"object_type": "cabinet", "bbox": [0.1, 0.1, 0.3, 0.3], "page": 0}
    ]
    assert result["dropped"] == 0
    assert result["complete"] is True
    assert result["error"] is None


def test_bare_list_is_accepted_without_a_wrapper_object():
    text = json.dumps([{"label": "cabinet", "box": [0.0, 0.0, 1.0, 1.0]}])

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["complete"] is True


def test_single_box_object_not_wrapped_in_a_list_is_accepted():
    text = json.dumps({"label": "cabinet", "box": [0.0, 0.0, 1.0, 1.0]})

    result = parse(text)

    assert len(result["boxes"]) == 1


def test_page_is_stamped_onto_every_box():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0, 0, 1, 1]}]})

    result = parse(text, page=7)

    assert result["boxes"][0]["page"] == 7


def test_empty_objects_list_is_a_clean_empty_result():
    result = parse(json.dumps({"objects": []}))

    assert result == {"boxes": [], "dropped": 0, "complete": True, "error": None}


# --- Wrapping: fences and surrounding prose (does not affect `complete`) ------------


def test_fenced_code_block_is_unwrapped():
    text = (
        "Here you go:\n```json\n"
        + json.dumps({"objects": [{"label": "cabinet", "box": [0, 0, 1, 1]}]})
        + "\n```\nLet me know if you need anything else!"
    )

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["complete"] is True
    assert result["error"] is None


def test_plain_fence_without_json_language_tag_is_unwrapped():
    text = "```\n" + json.dumps({"objects": []}) + "\n```"

    result = parse(text)

    assert result["complete"] is True


def test_leading_and_trailing_prose_without_a_fence_is_stripped():
    text = "Sure, here's the JSON: " + json.dumps({"objects": []}) + " done!"

    result = parse(text)

    assert result["complete"] is True
    assert result["error"] is None


# --- Truncation -----------------------------------------------------------------


def test_truncated_mid_array_is_recovered_and_marked_incomplete():
    full = json.dumps(
        {
            "objects": [
                {"label": "cabinet", "box": [0.1, 0.1, 0.2, 0.2]},
                {"label": "cabinet", "box": [0.3, 0.1, 0.4, 0.2]},
            ]
        }
    )
    # Cut off partway through the second entry, as a token-limited reply would be.
    cut_at = full.index('"cabinet", "box": [0.3') + 10
    truncated = full[:cut_at]

    result = parse(truncated)

    assert len(result["boxes"]) == 1
    assert result["boxes"][0]["bbox"] == [0.1, 0.1, 0.2, 0.2]
    assert result["complete"] is False
    assert "truncated" in result["error"]


def test_truncated_mid_string_value_is_recovered():
    full = json.dumps({"objects": [{"label": "cabinet", "box": [0, 0, 1, 1]}]})
    truncated = full[: full.index("cabinet") + 3]  # cuts inside the label string

    result = parse(truncated)

    # Not necessarily any boxes recovered, but must not raise and must report honestly.
    assert result["complete"] is False
    assert isinstance(result["boxes"], list)


def test_completely_unparseable_text_returns_empty_result_with_error():
    result = parse("the model said something that isn't JSON at all")

    assert result == {
        "boxes": [],
        "dropped": 0,
        "complete": False,
        "error": "could not parse a JSON object or array out of the response text",
    }


# --- Malformed syntax: semicolons, trailing commas, comments ------------------------


def test_trailing_comma_before_closing_bracket_is_repaired():
    text = '{"objects": [{"label": "cabinet", "box": [0, 0, 1, 1]},]}'

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["complete"] is False
    assert "malformed" in result["error"]


def test_stray_semicolon_in_place_of_comma_is_repaired():
    text = '{"objects": [{"label": "cabinet"; "box": [0, 0, 1, 1]}]}'

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["complete"] is False


def test_semicolon_inside_a_string_value_is_left_alone():
    text = json.dumps({"objects": [{"label": "cabinet; upper", "box": [0, 0, 1, 1]}]})

    result = parse(text)

    assert result["boxes"][0]["object_type"] == "cabinet; upper"
    assert result["complete"] is True  # nothing needed repairing


def test_line_comment_is_stripped():
    text = (
        '{"objects": [\n'
        '  {"label": "cabinet", "box": [0, 0, 1, 1]} // TODO: verify\n'
        "]}"
    )

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["complete"] is False


def test_block_comment_is_stripped():
    text = '{"objects": [{"label": "cabinet", /* confidence: high */ "box": [0, 0, 1, 1]}]}'

    result = parse(text)

    assert len(result["boxes"]) == 1


# --- Label / bbox key aliases --------------------------------------------------------


def test_object_type_key_is_accepted_as_a_label_alias():
    text = json.dumps({"objects": [{"object_type": "cabinet", "bbox": [0, 0, 1, 1]}]})

    result = parse(text)

    assert result["boxes"][0]["object_type"] == "cabinet"


def test_bbox_key_is_accepted_as_a_box_alias():
    text = json.dumps({"objects": [{"label": "cabinet", "bbox": [0, 0, 1, 1]}]})

    result = parse(text)

    assert len(result["boxes"]) == 1


# --- allowed_labels: dropping invented labels ---------------------------------------


def test_label_outside_allowed_labels_is_dropped():
    text = json.dumps(
        {
            "objects": [
                {"label": "cabinet", "box": [0, 0, 1, 1]},
                {"label": "spaceship", "box": [0, 0, 1, 1]},  # invented
            ]
        }
    )

    result = parse(text, allowed_labels={"cabinet", "countertop"})

    assert len(result["boxes"]) == 1
    assert result["boxes"][0]["object_type"] == "cabinet"
    assert result["dropped"] == 1


def test_allowed_labels_none_accepts_any_label():
    text = json.dumps({"objects": [{"label": "anything_goes", "box": [0, 0, 1, 1]}]})

    result = parse(text, allowed_labels=None)

    assert len(result["boxes"]) == 1


# --- Structurally invalid entries: dropped, not repaired -----------------------------


def test_entry_missing_a_label_is_dropped():
    text = json.dumps({"objects": [{"box": [0, 0, 1, 1]}]})

    result = parse(text)

    assert result["boxes"] == []
    assert result["dropped"] == 1


def test_entry_missing_a_bbox_is_dropped():
    text = json.dumps({"objects": [{"label": "cabinet"}]})

    result = parse(text)

    assert result["dropped"] == 1


def test_bbox_with_wrong_number_of_coordinates_is_dropped():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0, 0, 1]}]})

    result = parse(text)

    assert result["dropped"] == 1


def test_non_numeric_coordinate_is_dropped():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0, 0, 1, "north"]}]})

    result = parse(text)

    assert result["dropped"] == 1


def test_nan_coordinate_is_dropped():
    text = '{"objects": [{"label": "cabinet", "box": [0, 0, 1, NaN]}]}'

    result = parse(text)

    assert result["dropped"] == 1


def test_bool_in_a_bbox_slot_is_dropped_not_read_as_zero_or_one():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0, 0, 1, True]}]})

    result = parse(text)

    assert result["dropped"] == 1


def test_string_coordinate_that_parses_as_a_number_is_accepted():
    text = json.dumps({"objects": [{"label": "cabinet", "box": ["0", "0", "1", "1"]}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.0, 0.0, 1.0, 1.0]


def test_non_dict_entry_in_the_list_is_dropped():
    text = json.dumps(
        {"objects": ["just a string", {"label": "cabinet", "box": [0, 0, 1, 1]}]}
    )

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["dropped"] == 1


def test_one_bad_entry_does_not_drop_the_others():
    text = json.dumps(
        {
            "objects": [
                {"label": "cabinet", "box": [0, 0, 1, 1]},
                {"label": "cabinet"},  # missing bbox
                {"label": "countertop", "box": [0.2, 0.2, 0.5, 0.5]},
            ]
        }
    )

    result = parse(text)

    assert len(result["boxes"]) == 2
    assert result["dropped"] == 1


# --- Coordinate order: swapped back into place, never dropped ----------------------


def test_inverted_box_is_swapped_into_order_not_dropped():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0.5, 0.5, 0.1, 0.1]}]})

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["boxes"][0]["bbox"] == [0.1, 0.1, 0.5, 0.5]
    assert result["dropped"] == 0


def test_only_x_inverted_is_swapped_y_left_alone():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0.5, 0.1, 0.1, 0.5]}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.1, 0.1, 0.5, 0.5]


def test_zero_area_box_is_kept_not_dropped():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0.5, 0.5, 0.5, 0.5]}]})

    result = parse(text)

    assert len(result["boxes"]) == 1


# --- Exact duplicates: dropped ---------------------------------------------------


def test_exact_duplicate_entry_is_dropped():
    text = json.dumps(
        {
            "objects": [
                {"label": "cabinet", "box": [0.1, 0.1, 0.3, 0.3]},
                {"label": "cabinet", "box": [0.1, 0.1, 0.3, 0.3]},  # verbatim repeat
            ]
        }
    )

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["dropped"] == 1


def test_same_bbox_different_label_is_not_a_duplicate():
    text = json.dumps(
        {
            "objects": [
                {"label": "cabinet", "box": [0.1, 0.1, 0.3, 0.3]},
                {"label": "countertop", "box": [0.1, 0.1, 0.3, 0.3]},
            ]
        }
    )

    result = parse(text)

    assert len(result["boxes"]) == 2
    assert result["dropped"] == 0


def test_duplicate_after_coordinate_swap_is_still_caught():
    text = json.dumps(
        {
            "objects": [
                {"label": "cabinet", "box": [0.1, 0.1, 0.3, 0.3]},
                {"label": "cabinet", "box": [0.3, 0.3, 0.1, 0.1]},  # same box, inverted
            ]
        }
    )

    result = parse(text)

    assert len(result["boxes"]) == 1
    assert result["dropped"] == 1


# --- Alternate bbox shapes: named keys and nested identifying_properties -----------


def test_left_top_right_bottom_named_keys_are_accepted():
    text = json.dumps(
        {
            "objects": [
                {
                    "label": "cabinet",
                    "left": 0.1,
                    "top": 0.2,
                    "right": 0.3,
                    "bottom": 0.4,
                }
            ]
        }
    )

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_x0_y0_x1_y1_named_keys_are_accepted():
    text = json.dumps(
        {"objects": [{"label": "cabinet", "x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.4}]}
    )

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_nested_identifying_properties_bbox_is_accepted():
    text = json.dumps(
        {
            "objects": [
                {
                    "label": "cabinet",
                    "identifying_properties": {
                        "left": 0.1,
                        "top": 0.2,
                        "right": 0.3,
                        "bottom": 0.4,
                    },
                }
            ]
        }
    )

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_box_key_is_preferred_over_named_keys_when_both_present():
    text = json.dumps(
        {
            "objects": [
                {
                    "label": "cabinet",
                    "box": [0.9, 0.9, 1.0, 1.0],
                    "left": 0.0,
                    "top": 0.0,
                    "right": 0.1,
                    "bottom": 0.1,
                }
            ]
        }
    )

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.9, 0.9, 1.0, 1.0]


# --- Coordinate scale auto-detection --------------------------------------------------


def test_0_to_1000_scale_box_is_rescaled_to_0_to_1():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [100, 200, 300, 400]}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_0_to_1_scale_box_is_passed_through_unchanged():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0.1, 0.2, 0.3, 0.4]}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_coordinate_of_exactly_1_is_treated_as_0_to_1_scale():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0.0, 0.0, 1.0, 1.0]}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.0, 0.0, 1.0, 1.0]


def test_near_edge_overflow_past_1_is_not_misread_as_1000_scale():
    # A box that's genuinely 0-1 scale but slightly overflows past 1.0 near an edge
    # (floating-point or model imprecision) must be passed through as-is, not
    # reinterpreted as a barely-perceptible 1000-scale box and shrunk to a corner.
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0.8, 0.9, 1.05, 1.0]}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.8, 0.9, 1.05, 1.0]


@pytest.mark.parametrize(
    "overflowing_coordinate",
    [1.0001, 1.01, 1.05, 1.2, 1.5, 1.8, 1.999, 2.0],
    ids=lambda v: f"overflow_{v}",
)
def test_a_range_of_near_edge_overflows_all_stay_unrescaled(overflowing_coordinate):
    # Sweeps the whole "just past 1.0, up to and including the threshold itself"
    # range -- not just one hand-picked value -- to confirm none of them get
    # mistaken for 1000-scale. 2.0 itself is included as the exact boundary: the
    # rescale check is strictly-greater-than, so the threshold value itself must
    # still be treated as 0-1 scale, not rescaled.
    box = [0.1, 0.1, overflowing_coordinate, 0.5]
    text = json.dumps({"objects": [{"label": "cabinet", "box": box}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == box


@pytest.mark.parametrize(
    "past_threshold_coordinate",
    [2.0001, 2.5, 3, 10, 50, 100, 500, 999, 1000],
    ids=lambda v: f"past_{v}",
)
def test_a_range_of_magnitudes_clearly_past_threshold_are_all_rescaled(
    past_threshold_coordinate,
):
    # Sweeps small-but-unambiguous 1000-scale values (just past the threshold) up
    # through large ones (near the top of the 0-1000 range), confirming the whole
    # box is divided by 1000 at every magnitude, not just a hand-picked "big" one.
    box = [0, 0, past_threshold_coordinate, past_threshold_coordinate]
    text = json.dumps({"objects": [{"label": "cabinet", "box": box}]})

    result = parse(text)

    expected = [v / 1000.0 for v in box]
    assert result["boxes"][0]["bbox"] == expected


def test_coordinate_clearly_past_threshold_is_still_rescaled():
    text = json.dumps({"objects": [{"label": "cabinet", "box": [0, 0, 5, 8]}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"] == [0.0, 0.0, 0.005, 0.008]


@pytest.mark.parametrize(
    "negative_overflow,expected",
    [
        (
            -1.05,
            -1.05,
        ),  # negative near-edge overflow: abs(-1.05) = 1.05 <= threshold, stays as-is
        (-1.999, -1.999),  # still within the near-edge range
        (
            -2.5,
            -0.0025,
        ),  # abs(-2.5) = 2.5 > threshold: whole box (incl. this value) gets rescaled
        (-500, -0.5),  # clearly 1000-scale, negative -- still rescaled by /1000
    ],
)
def test_scale_detection_uses_absolute_value_for_negative_coordinates(
    negative_overflow, expected
):
    # The threshold check is abs(value) > _SCALE_THRESHOLD, so a negative coordinate
    # is judged by its magnitude, not its sign -- a small negative overflow (e.g. a
    # box extending slightly left of x=0) must not be treated as 1000-scale just
    # because it's negative, and a genuinely large negative 1000-scale coordinate
    # must still be rescaled.
    box = [negative_overflow, 0.1, 0.5, 0.5]
    text = json.dumps({"objects": [{"label": "cabinet", "box": box}]})

    result = parse(text)

    assert result["boxes"][0]["bbox"][0] == expected


@pytest.mark.parametrize(
    "box",
    [
        [0.1, 0.1, 0.5, 250],  # only y_max is clearly 1000-scale
        [
            300,
            0.1,
            500,
            0.5,
        ],  # x_min is clearly 1000-scale (x_max too, order preserved)
        [
            0.1,
            400,
            0.5,
            600,
        ],  # y_min is clearly 1000-scale (y_max too, order preserved)
        [0.1, 0.1, 600, 0.5],  # only x_max is clearly 1000-scale
    ],
    ids=["y_max", "x_min", "y_min", "x_max"],
)
def test_a_single_past_threshold_coordinate_rescales_the_whole_box_regardless_of_position(
    box,
):
    # The scale decision is made once for the whole box, from whichever coordinate
    # triggers it -- confirmed here at each of the four positions, not just one.
    text = json.dumps({"objects": [{"label": "cabinet", "box": box}]})

    result = parse(text)

    expected = [v / 1000.0 for v in box]
    assert result["boxes"][0]["bbox"] == expected


# --- Result shape ---------------------------------------------------------------


def test_result_is_a_plain_json_serializable_dict():
    result = parse(json.dumps({"objects": []}))

    assert json.dumps(result)  # does not raise


def test_container_key_aliases_boxes_detections_predictions_results():
    for key in ("boxes", "detections", "predictions", "results"):
        text = json.dumps({key: [{"label": "cabinet", "box": [0, 0, 1, 1]}]})
        result = parse(text)
        assert len(result["boxes"]) == 1, f"container key {key!r} was not recognized"


# --- Performance: truncation repair must stay fast on large, dense replies ----------


def test_repair_stays_fast_on_a_large_dense_truncated_reply():
    """Regression test for the PR review concern that repair_truncated's trim loop
    could be O(max_trim x len(candidate)) -- benchmarked at 5+ seconds on a ~26KB
    pathological input before the fix (a precomputed bracket-state table replacing a
    fresh rescan per trim attempt). The realistic case this matters for -- a reply
    truncated mid-value partway through a dense array -- must resolve in well under a
    second, not several."""
    import time

    entries = ",".join(
        json.dumps({"label": "cabinet", "box": [i, i, i + 20, i + 20]})
        for i in range(500)
    )
    # cut off mid-value, deep into a long trailing string field -- the realistic
    # truncation shape (a model running out of output tokens), not a syntax error
    # injected into otherwise-complete JSON.
    candidate = (
        '{"objects": [' + entries + ', {"label": "cabinet", "note": "' + ("a" * 1990)
    )

    start = time.perf_counter()
    result = parse(candidate)
    elapsed = time.perf_counter() - start

    assert (
        elapsed < 1.0
    ), f"repair took {elapsed:.3f}s on a dense truncated reply -- expected well under 1s"
    assert len(result["boxes"]) == 500  # the trailing incomplete entry is discarded
    assert result["complete"] is False
    