from typing import Sequence

from ._matching import Match, Matching
from ._types import Box, FalseNegative, FalsePositive, ObjectsBreakdown, TruePositive


def breakdown(
    predictions: Sequence[Box],
    ground_truth: Sequence[Box],
    matching: Matching,
) -> ObjectsBreakdown:
    return {
        "tp": [
            _true_positive(predictions, ground_truth, match)
            for match in sorted(matching.matched, key=lambda m: m.prediction_index)
        ],
        "fp": [
            _false_positive(
                predictions[index], index, matching.near_miss.predictions[index]
            )
            for index in matching.unmatched_predictions
        ],
        "fn": [
            _false_negative(
                ground_truth[index], index, matching.near_miss.ground_truth[index]
            )
            for index in matching.unmatched_ground_truth
        ],
    }


def _true_positive(
    predictions: Sequence[Box], ground_truth: Sequence[Box], match: Match
) -> TruePositive:
    prediction = predictions[match.prediction_index]
    return {
        "page": prediction["page"],
        "object_type": prediction["object_type"],
        "prediction_index": match.prediction_index,
        "ground_truth_index": match.ground_truth_index,
        "prediction": list(prediction["bbox"]),
        "ground_truth": list(ground_truth[match.ground_truth_index]["bbox"]),
        "iou": match.iou,
    }


def _false_positive(prediction: Box, index: int, best_iou: float) -> FalsePositive:
    return {
        "page": prediction["page"],
        "object_type": prediction["object_type"],
        "prediction_index": index,
        "prediction": list(prediction["bbox"]),
        "best_iou": best_iou,
    }


def _false_negative(box: Box, index: int, best_iou: float) -> FalseNegative:
    return {
        "page": box["page"],
        "object_type": box["object_type"],
        "ground_truth_index": index,
        "ground_truth": list(box["bbox"]),
        "best_iou": best_iou,
    }
