"""Tests for the library's entire public surface: ``location_parser.parse()``.

Everything below ``parse()`` -- extraction, sanitization, truncation repair, box
validation -- is private and is covered here through its effect on the returned
result. Nothing in these tests names an internal helper, so the tests describe
*parsing behaviour* and stay valid through any refactor of the internals.
"""

import json

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


# --- Result shape ---------------------------------------------------------------


def test_result_is_a_plain_json_serializable_dict():
    result = parse(json.dumps({"objects": []}))

    assert json.dumps(result)  # does not raise


def test_container_key_aliases_boxes_detections_predictions_results():
    for key in ("boxes", "detections", "predictions", "results"):
        text = json.dumps({key: [{"label": "cabinet", "box": [0, 0, 1, 1]}]})
        result = parse(text)
        assert len(result["boxes"]) == 1, f"container key {key!r} was not recognized"
