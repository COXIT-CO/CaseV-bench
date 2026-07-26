"""Tests for the library's entire public surface: ``location_scorer.score()``.

Everything below ``score()`` — the IoU computation, the greedy matcher, the metric
arithmetic — is private and is covered here through its effect on the returned result.
Nothing in these tests names an internal helper or asserts the shape of an intermediate,
so the tests describe *scoring behaviour* and stay valid through any refactor of the
internals.

Test data is hand-constructed so the IoU is arithmetically obvious: the ground-truth box
is the unit square, so a prediction spanning the full height and a fraction ``w`` of the
width has ``intersection = w``, ``union = 1``, and therefore ``IoU = w``.
"""

import json

import pytest

from location_scorer import score

CABINET = "cabinet"
COUNTERTOP = "countertop"


def box(x_min, y_min, x_max, y_max, object_type=CABINET, page=1):
    return {
        "object_type": object_type,
        "bbox": [x_min, y_min, x_max, y_max],
        "page": page,
    }


def unit(object_type=CABINET, page=1):
    return box(0, 0, 1, 1, object_type=object_type, page=page)


def overlapping(width, object_type=CABINET, page=1):
    """Full height over the unit square, so the IoU equals ``width``."""
    return box(0, 0, width, 1, object_type=object_type, page=page)


# --- Overall counts and rates -------------------------------------------------------


def test_perfect_match_scores_one():
    result = score([unit()], [unit()], iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 0, "fn": 0}
    assert result["metrics"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_counts_and_rates_are_reported_together():
    ground_truth = [unit(), box(2, 0, 3, 1)]
    predictions = [unit(), box(5, 0, 6, 1)]  # one hit, one hallucination

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 1, "fn": 1}
    assert result["metrics"]["precision"] == 0.5
    assert result["metrics"]["recall"] == 0.5
    assert result["metrics"]["f1"] == 0.5


def test_threshold_is_echoed_back():
    result = score([], [], iou_threshold=0.42)

    assert result["iou_threshold"] == 0.42


def test_no_metric_is_named_accuracy():
    result = score([unit()], [unit()], iou_threshold=0.5)

    assert "accuracy" not in result["metrics"]
    assert "accuracy" not in result


def test_result_is_a_plain_json_serializable_dict():
    result = score([unit()], [unit()], iou_threshold=0.5)

    assert isinstance(result, dict)
    assert json.loads(json.dumps(result)) == result


def test_objects_breakdown_is_absent_when_not_requested():
    assert "objects" not in score([unit()], [unit()], iou_threshold=0.5)


# --- Threshold boundary -------------------------------------------------------------


def test_iou_just_above_threshold_matches():
    result = score([overlapping(0.51)], [unit()], iou_threshold=0.5)

    assert result["counts"]["tp"] == 1


def test_iou_exactly_at_threshold_matches():
    # A 0.5-wide prediction over the unit square has IoU exactly 0.5; the rule is `>=`.
    result = score([overlapping(0.5)], [unit()], iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 0, "fn": 0}


def test_iou_just_below_threshold_does_not_match():
    result = score([overlapping(0.49)], [unit()], iou_threshold=0.5)

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 1}


# --- Partitioning: page and object type ---------------------------------------------


def test_prediction_never_matches_ground_truth_on_another_page():
    # Identical geometry — only the page differs.
    result = score([unit(page=1)], [unit(page=5)], iou_threshold=0.5)

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 1}


def test_prediction_never_matches_ground_truth_of_another_object_type():
    result = score(
        [unit(object_type=COUNTERTOP)], [unit(object_type=CABINET)], iou_threshold=0.5
    )

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 1}


def test_pages_are_scored_independently_and_pooled():
    ground_truth = [unit(page=1), unit(page=2)]
    predictions = [unit(page=1), overlapping(0.2, page=2)]

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 1, "fn": 1}


# --- One-to-one matching ------------------------------------------------------------


def test_duplicate_predictions_yield_one_true_positive_and_one_false_positive():
    result = score([unit(), unit()], [unit()], iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 1, "fn": 0}


def test_one_prediction_cannot_cover_several_ground_truth_boxes():
    # A wide prediction overlapping two adjacent unit squares, clearing the threshold
    # against both: intersection 1, union 2 + 1 - 1 = 2, IoU 0.5 each.
    ground_truth = [box(0, 0, 1, 1), box(1, 0, 2, 1)]
    predictions = [box(0, 0, 2, 1)]

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 0, "fn": 1}


def test_tightest_pair_wins_the_assignment():
    # Both predictions clear the threshold against the single ground-truth box; the
    # tighter one takes it regardless of input order.
    result = score([overlapping(0.6), overlapping(0.9)], [unit()], iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 1, "fn": 0}


# --- Tie-break direction ------------------------------------------------------------


def test_equal_iou_candidates_resolve_to_the_lowest_indices():
    # Three candidate pairs tie at IoU 0.6, the fourth is below threshold:
    #
    #            gt0    gt1
    #   pred0    0.60   0.60
    #   pred1    0.60   0.23
    #
    # Lowest-index tie-break takes (pred0, gt0) first, which strands pred1 (its only
    # remaining candidate, gt1, is below threshold) and leaves gt1 unmatched.
    # A highest-index tie-break would take (pred1, gt0) then (pred0, gt1) and score 2 TP,
    # so this asserts the direction, not merely that ties are broken somehow.
    ground_truth = [box(0, 0, 1, 1), box(0.5, 0, 1.5, 1)]
    predictions = [box(0.25, 0, 1.25, 1), box(0, 0, 1, 0.6)]

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["counts"] == {"tp": 1, "fp": 1, "fn": 1}


def test_reordering_identical_boxes_does_not_change_the_score():
    ground_truth = [unit(), unit(), box(2, 0, 3, 1)]
    predictions = [unit(), unit(), overlapping(0.9)]

    forward = score(predictions, ground_truth, iou_threshold=0.5)
    reversed_ = score(predictions[::-1], ground_truth[::-1], iou_threshold=0.5)

    assert forward == reversed_


# --- Micro-averaging ----------------------------------------------------------------


def test_rates_are_micro_averaged_from_pooled_tallies():
    # cabinet: 3 GT, 3 hits          -> recall 1.0
    # countertop: 1 GT, 0 hits       -> recall 0.0
    # Macro recall would be 0.5; micro recall is 3/4 = 0.75.
    ground_truth = [
        box(0, 0, 1, 1),
        box(2, 0, 3, 1),
        box(4, 0, 5, 1),
        unit(object_type=COUNTERTOP),
    ]
    predictions = [box(0, 0, 1, 1), box(2, 0, 3, 1), box(4, 0, 5, 1)]

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["counts"] == {"tp": 3, "fp": 0, "fn": 1}
    assert result["metrics"]["recall"] == 0.75
    assert result["metrics"]["precision"] == 1.0


# --- Per-object-type breakdown ------------------------------------------------------


def test_per_type_carries_its_own_counts_and_rates_for_every_type():
    ground_truth = [unit(), unit(object_type=COUNTERTOP)]
    predictions = [unit(), overlapping(0.2, object_type=COUNTERTOP)]

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["per_type"] == {
        CABINET: {
            "counts": {"tp": 1, "fp": 0, "fn": 0},
            "metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        },
        COUNTERTOP: {
            "counts": {"tp": 0, "fp": 1, "fn": 1},
            "metrics": {"precision": 0.0, "recall": 0.0, "f1": 0.0},
        },
    }


def test_type_present_only_in_ground_truth_appears_with_zero_recall():
    result = score([unit()], [unit(), unit(object_type=COUNTERTOP)], iou_threshold=0.5)

    assert result["per_type"][COUNTERTOP]["counts"] == {"tp": 0, "fp": 0, "fn": 1}
    assert result["per_type"][COUNTERTOP]["metrics"]["recall"] == 0.0


def test_type_present_only_in_predictions_appears_with_zero_precision():
    result = score([unit(), unit(object_type=COUNTERTOP)], [unit()], iou_threshold=0.5)

    assert result["per_type"][COUNTERTOP]["counts"] == {"tp": 0, "fp": 1, "fn": 0}
    assert result["per_type"][COUNTERTOP]["metrics"]["precision"] == 0.0


def test_per_type_keys_are_only_the_types_present_on_one_side_or_the_other():
    result = score([unit()], [unit()], iou_threshold=0.5)

    assert set(result["per_type"]) == {CABINET}


def test_per_type_entry_pools_that_type_across_every_page():
    # cabinet: 3 of 3 hit on page 1, 0 of 1 on page 2. Macro over pages 0.5, micro 3/4.
    ground_truth = [
        box(0, 0, 1, 1),
        box(2, 0, 3, 1),
        box(4, 0, 5, 1),
        unit(page=2),
    ]
    predictions = [box(0, 0, 1, 1), box(2, 0, 3, 1), box(4, 0, 5, 1)]

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["per_type"][CABINET]["counts"] == {"tp": 3, "fp": 0, "fn": 1}
    assert result["per_type"][CABINET]["metrics"]["recall"] == 0.75


# --- Per-page breakdown -------------------------------------------------------------


def test_per_page_is_a_list_ordered_by_page():
    ground_truth = [unit(page=5), unit(page=1), unit(page=3)]

    result = score([], ground_truth, iou_threshold=0.5)

    assert [entry["page"] for entry in result["per_page"]] == [1, 3, 5]


def test_page_entry_repeats_the_top_level_block_shape():
    result = score([unit(page=2)], [unit(page=2)], iou_threshold=0.5)

    assert result["per_page"] == [
        {
            "page": 2,
            "counts": {"tp": 1, "fp": 0, "fn": 0},
            "metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
            "per_type": {
                CABINET: {
                    "counts": {"tp": 1, "fp": 0, "fn": 0},
                    "metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
                }
            },
        }
    ]


def test_per_page_covers_pages_seen_on_either_side():
    result = score([unit(page=1)], [unit(page=2)], iou_threshold=0.5)

    assert [entry["page"] for entry in result["per_page"]] == [1, 2]
    assert result["per_page"][0]["counts"] == {"tp": 0, "fp": 1, "fn": 0}
    assert result["per_page"][1]["counts"] == {"tp": 0, "fp": 0, "fn": 1}


def test_page_entry_pools_every_type_on_that_page():
    # page 1: 3 of 3 cabinets hit, 0 of 1 countertop. Macro over types 0.5, micro 3/4.
    ground_truth = [
        box(0, 0, 1, 1),
        box(2, 0, 3, 1),
        box(4, 0, 5, 1),
        unit(object_type=COUNTERTOP),
    ]
    predictions = [box(0, 0, 1, 1), box(2, 0, 3, 1), box(4, 0, 5, 1)]

    page = score(predictions, ground_truth, iou_threshold=0.5)["per_page"][0]

    assert page["counts"] == {"tp": 3, "fp": 0, "fn": 1}
    assert page["metrics"]["recall"] == 0.75


def test_page_entry_says_which_type_failed_on_that_page():
    # Same mix on both pages, so only the page's own per-type block localizes the failure.
    ground_truth = [unit(object_type=COUNTERTOP, page=p) for p in (1, 2)] + [
        unit(page=p) for p in (1, 2)
    ]
    predictions = [unit(object_type=COUNTERTOP, page=1), unit(page=1), unit(page=2)]

    result = score(predictions, ground_truth, iou_threshold=0.5)
    first, second = result["per_page"]

    assert first["per_type"][COUNTERTOP]["counts"] == {"tp": 1, "fp": 0, "fn": 0}
    assert second["per_type"][COUNTERTOP]["counts"] == {"tp": 0, "fp": 0, "fn": 1}
    assert second["per_type"][CABINET]["counts"] == {"tp": 1, "fp": 0, "fn": 0}


def test_page_values_survive_a_json_round_trip_as_integers():
    # A page-keyed dict would come back keyed by the string "10" instead.
    result = score([unit(page=10)], [unit(page=10)], iou_threshold=0.5)

    reloaded = json.loads(json.dumps(result))

    assert reloaded == result
    assert reloaded["per_page"][0]["page"] == 10


# --- Empty and zero-denominator inputs ----------------------------------------------


def test_no_predictions_scores_every_ground_truth_box_as_a_false_negative():
    result = score([], [unit(), unit(page=2)], iou_threshold=0.5)

    assert result["counts"] == {"tp": 0, "fp": 0, "fn": 2}
    assert result["metrics"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_no_ground_truth_returns_a_well_formed_result():
    result = score([unit()], [], iou_threshold=0.5)

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 0}
    assert result["metrics"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_both_sides_empty_yields_all_zero_counts():
    result = score([], [], iou_threshold=0.5)

    assert result["counts"] == {"tp": 0, "fp": 0, "fn": 0}
    assert result["metrics"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert result["per_type"] == {}
    assert result["per_page"] == []


# --- Degenerate predictions: lenient, never rejected --------------------------------


def test_zero_area_prediction_is_a_false_positive_rather_than_a_match():
    result = score([box(0, 0, 0, 0)], [unit()], iou_threshold=0.5)

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 1}


def test_zero_iou_is_never_a_match_even_at_a_zero_threshold():
    # `IoU >= threshold` alone would credit a prediction that misses entirely once the
    # threshold reaches 0.0; the zero-IoU floor is what stops that, and it is documented
    # on `score()` so a reimplementation lands on the same numbers.
    result = score([box(9, 9, 10, 10)], [unit()], iou_threshold=0.0)

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 1}


def test_inverted_prediction_is_a_false_positive_rather_than_a_match():
    result = score([box(1, 1, 0, 0)], [unit()], iou_threshold=0.5)

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 1}


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param(box(1, 0, 0, 1), id="inverted-horizontally"),
        pytest.param(box(0, 1, 1, 0), id="inverted-vertically"),
        pytest.param(box(0.5, 0, 0.5, 1), id="zero-width"),
        pytest.param(box(0, 0.5, 1, 0.5), id="zero-height"),
    ],
)
def test_a_degenerate_prediction_cannot_match_even_at_the_loosest_threshold(malformed):
    # 0.0 is the most permissive operating point there is, so no threshold admits what it
    # rejects.
    result = score([malformed], [unit()], iou_threshold=0.0)

    assert result["counts"] == {"tp": 0, "fp": 1, "fn": 1}


def test_a_batch_peppered_with_malformed_predictions_still_scores():
    # One bad box must not abort a whole run — salvaged predictions are scored (ADR 0027).
    ground_truth = [box(i, 0, i + 1, 1) for i in range(100)]
    predictions = [
        box(i, 0, i, 1) if i % 10 == 0 else box(i, 0, i + 1, 1) for i in range(100)
    ]

    result = score(predictions, ground_truth, iou_threshold=0.5)

    assert result["counts"] == {"tp": 90, "fp": 10, "fn": 10}
    assert result["metrics"]["f1"] == 0.9


# --- Degenerate ground truth: strict, raises ----------------------------------------


@pytest.mark.parametrize(
    "malformed, condition",
    [
        pytest.param(box(1, 0, 0, 1), "inverted", id="inverted-horizontally"),
        pytest.param(box(0, 1, 1, 0), "inverted", id="inverted-vertically"),
        pytest.param(box(0.5, 0, 0.5, 1), "zero width", id="zero-width"),
        pytest.param(box(0, 0.5, 1, 0.5), "zero height", id="zero-height"),
    ],
)
def test_a_degenerate_ground_truth_box_raises(malformed, condition):
    # Unmatchable by anything, so left alone it is an invisible permanent FN (ADR 0031).
    with pytest.raises(ValueError) as raised:
        score([unit()], [malformed], iou_threshold=0.5)

    assert condition in str(raised.value)


def test_the_error_names_the_offending_index_so_it_can_be_found_in_the_source_data():
    ground_truth = [unit(), unit(page=2), box(1, 1, 0, 0, page=3)]

    with pytest.raises(ValueError) as raised:
        score([], ground_truth, iou_threshold=0.5)

    assert "ground_truth[2]" in str(raised.value)


def test_a_ground_truth_box_is_validated_even_where_nothing_was_predicted():
    # Page 3 has no prediction, so a check folded into the matcher would never reach it and
    # the defect would hide behind a clean score for pages 1 and 2.
    ground_truth = [unit(), unit(page=2), box(0.5, 0.5, 0.5, 0.5, page=3)]
    predictions = [unit(), unit(page=2)]

    with pytest.raises(ValueError):
        score(predictions, ground_truth, iou_threshold=0.5)


def test_identical_zero_area_boxes_raise_rather_than_matching_each_other():
    # Their union is zero, so only the ground-truth check stands between them and an
    # undefined 0/0 read as a "perfect" overlap.
    with pytest.raises(ValueError):
        score([box(0.5, 0.5, 0.5, 0.5)], [box(0.5, 0.5, 0.5, 0.5)], iou_threshold=0.5)


# --- Purity -------------------------------------------------------------------------


def test_same_inputs_return_equal_results():
    predictions = [overlapping(0.9), overlapping(0.6, page=2)]
    ground_truth = [unit(), unit(page=2)]

    first = score(predictions, ground_truth, iou_threshold=0.5)
    second = score(predictions, ground_truth, iou_threshold=0.5)

    assert first == second


def test_caller_inputs_are_not_mutated():
    predictions = [overlapping(0.9), box(0, 0, 0, 0)]
    ground_truth = [unit(), unit(page=2)]
    predictions_before = json.dumps(predictions)
    ground_truth_before = json.dumps(ground_truth)

    score(predictions, ground_truth, iou_threshold=0.5)

    assert json.dumps(predictions) == predictions_before
    assert json.dumps(ground_truth) == ground_truth_before
    assert len(predictions) == 2 and len(ground_truth) == 2


def test_tuples_are_accepted_for_both_sides():
    result = score((unit(),), (unit(),), iou_threshold=0.5)

    assert result["counts"]["tp"] == 1
