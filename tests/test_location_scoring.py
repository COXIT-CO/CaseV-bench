"""Tests for app/location_scoring.py — the adapter between this app's internal
merged_objects format (label/box/image_index) and location-scorer's format
(object_type/bbox/page).

The scoring numbers below are the worked example from location-scorer's own
README (0.1.0), used here as a known-good fixture so a refactor of the adapter
that silently changes what gets passed to score() would be caught. Real
ground truth in this app must use 0..1000 coordinates (see location_scoring.py
module docstring); the 0..1 scale here is only to match the documented,
independently-verified expected numbers.
"""

import pytest

from app.location_scoring import is_bbox_ground_truth, score_location, to_score_items


def test_to_score_items_renames_fields_without_touching_values():
    objects = [
        {"label": "cabinet", "left": 100, "top": 100, "right": 300, "bottom": 300,
         "box": [100, 100, 300, 300], "image_index": 0},
        {"label": "countertop", "left": 10, "top": 600, "right": 900, "bottom": 700,
         "box": [10, 600, 900, 700], "image_index": 1},
    ]

    assert to_score_items(objects) == [
        {"object_type": "cabinet", "bbox": [100, 100, 300, 300], "page": 0},
        {"object_type": "countertop", "bbox": [10, 600, 900, 700], "page": 1},
    ]


def test_to_score_items_on_empty_list():
    assert to_score_items([]) == []


@pytest.mark.parametrize("data, expected", [
    ([{"object_type": "cabinet", "bbox": [0, 0, 1, 1], "page": 0}], True),
    ([], True),  # a list is still the bbox-format signature even with zero ground truth
    ({"cabinets": 5, "countertops": 3}, False),  # legacy counts dict
    (None, False),
    ([{"label": "cabinet"}], False),  # list, but missing bbox/object_type keys
])
def test_is_bbox_ground_truth(data, expected):
    assert is_bbox_ground_truth(data) is expected


def test_score_location_matches_location_scorer_worked_example():
    # Internal merged_objects-shaped predictions (label/box/image_index),
    # equivalent to location-scorer README's `predictions` list.
    predicted_objects = [
        {"label": "cabinet", "box": [0.10, 0.10, 0.30, 0.30], "image_index": 1},  # exact hit
        {"label": "cabinet", "box": [0.50, 0.10, 0.58, 0.30], "image_index": 1},  # IoU 0.4 — too loose
        {"label": "cabinet", "box": [0.10, 0.10, 0.30, 0.30], "image_index": 1},  # duplicate of the first
        {"label": "cabinet", "box": [0.20, 0.20, 0.40, 0.40], "image_index": 2},  # exact hit
    ]
    # Already in location-scorer's own format, as it would come straight out of
    # a Prompt.expected_json list.
    ground_truth = [
        {"object_type": "cabinet", "bbox": [0.10, 0.10, 0.30, 0.30], "page": 1},
        {"object_type": "cabinet", "bbox": [0.50, 0.10, 0.70, 0.30], "page": 1},
        {"object_type": "countertop", "bbox": [0.10, 0.60, 0.90, 0.70], "page": 1},
        {"object_type": "cabinet", "bbox": [0.20, 0.20, 0.40, 0.40], "page": 2},
    ]

    result = score_location(predicted_objects, ground_truth, iou_threshold=0.5)

    assert result["iou_threshold"] == 0.5
    assert result["counts"] == {"tp": 2, "fp": 2, "fn": 2}
    assert result["metrics"] == {"precision": 0.5, "recall": 0.5, "f1": 0.5}

    assert result["per_type"]["cabinet"]["counts"] == {"tp": 2, "fp": 2, "fn": 1}
    assert result["per_type"]["cabinet"]["metrics"]["precision"] == pytest.approx(0.5)
    assert result["per_type"]["cabinet"]["metrics"]["recall"] == pytest.approx(0.667, abs=1e-3)
    assert result["per_type"]["cabinet"]["metrics"]["f1"] == pytest.approx(0.571, abs=1e-3)

    assert result["per_type"]["countertop"]["counts"] == {"tp": 0, "fp": 0, "fn": 1}
    assert result["per_type"]["countertop"]["metrics"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    # score_location defaults include_objects=True, since the UI shows the
    # per-object breakdown behind a collapsible details block.
    assert "objects" in result
    assert len(result["objects"]["tp"]) == result["counts"]["tp"]
    assert len(result["objects"]["fp"]) == result["counts"]["fp"]
    assert len(result["objects"]["fn"]) == result["counts"]["fn"]


def test_score_location_empty_inputs_score_zero_not_error():
    result = score_location([], [], iou_threshold=0.5)
    assert result["counts"] == {"tp": 0, "fp": 0, "fn": 0}
    assert result["metrics"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
