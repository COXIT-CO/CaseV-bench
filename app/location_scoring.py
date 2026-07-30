from __future__ import annotations

from typing import Any

from location_scorer import score

# Ground-truth bbox coordinates must be in the SAME 0..1000 normalized scale as
# everything else in this app (see result_parser.py / annotate.py / crop_utils.py) —
# NOT the 0..1 scale used in location-scorer's own README examples. A scale mismatch
# doesn't raise an error, it just makes every IoU ~0 (see the tp==0 guard in
# services.py::process_prompt_run for the runtime safety net).


def is_bbox_ground_truth(data: Any) -> bool:
    """True if expected_json holds a list of {object_type, bbox, page} items
    rather than the legacy {label: count} dict."""
    return isinstance(data, list) and all(
        isinstance(item, dict) and "bbox" in item and "object_type" in item
        for item in data
    )


def to_score_items(objects: list[dict]) -> list[dict]:
    """Adapt internal merged_objects format (label/box/image_index) to
    location-scorer's format (object_type/bbox/page). Does not touch or
    re-derive the objects themselves — pure field renaming, no rescaling."""
    return [
        {"object_type": obj["label"], "bbox": obj["box"], "page": obj["image_index"]}
        for obj in objects
    ]


def score_location(
    predicted_objects: list[dict],
    ground_truth: list[dict],
    iou_threshold: float,
    include_objects: bool = True,
) -> dict:
    """predicted_objects: internal merged_objects format (0..1000 coords).
    ground_truth: already in location-scorer format, straight from expected_json
    (must also be 0..1000 coords to match predicted_objects)."""
    predictions = to_score_items(predicted_objects)
    return score(predictions, ground_truth, iou_threshold=iou_threshold,
                 include_objects=include_objects)
