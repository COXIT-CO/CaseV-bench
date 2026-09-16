from typing import NamedTuple, Sequence

from ._types import Box, Cell, cell_of


class Match(NamedTuple):
    prediction_index: int
    ground_truth_index: int
    iou: float


class NearMiss(NamedTuple):
    """Highest IoU each box reached against the other side, matched or not.

    Indexed by input position; `0.0` where the other side holds no box of that cell. It falls
    out of the IoU matrix built here, so it is returned rather than recomputed elsewhere.
    """

    predictions: list[float]
    ground_truth: list[float]


class Matching(NamedTuple):
    matched: list[Match]
    unmatched_predictions: list[int]
    unmatched_ground_truth: list[int]
    near_miss: NearMiss


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    intersection = _area(
        (
            max(a[0], b[0]),
            max(a[1], b[1]),
            min(a[2], b[2]),
            min(a[3], b[3]),
        )
    )
    union = _area(a) + _area(b) - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def match(
    predictions: Sequence[Box],
    ground_truth: Sequence[Box],
    iou_threshold: float,
) -> Matching:
    matched: list[Match] = []
    used_predictions: set[int] = set()
    used_ground_truth: set[int] = set()
    best_prediction_iou = [0.0] * len(predictions)
    best_ground_truth_iou = [0.0] * len(ground_truth)

    prediction_partitions = _partition_by_cell(predictions)
    ground_truth_partitions = _partition_by_cell(ground_truth)

    for key in sorted(prediction_partitions.keys() & ground_truth_partitions.keys()):
        candidates = _candidates(
            predictions,
            ground_truth,
            prediction_partitions[key],
            ground_truth_partitions[key],
        )
        _record_near_miss(candidates, best_prediction_iou, best_ground_truth_iou)
        for candidate in candidates:
            # The zero check is not redundant: it is what stops a threshold of 0.0 crediting
            # boxes that do not overlap at all.
            if candidate.iou < iou_threshold or candidate.iou == 0.0:
                break
            if (
                candidate.prediction_index in used_predictions
                or candidate.ground_truth_index in used_ground_truth
            ):
                continue
            matched.append(candidate)
            used_predictions.add(candidate.prediction_index)
            used_ground_truth.add(candidate.ground_truth_index)

    return Matching(
        matched=matched,
        unmatched_predictions=[
            i for i in range(len(predictions)) if i not in used_predictions
        ],
        unmatched_ground_truth=[
            i for i in range(len(ground_truth)) if i not in used_ground_truth
        ],
        near_miss=NearMiss(
            predictions=best_prediction_iou, ground_truth=best_ground_truth_iou
        ),
    )


def _record_near_miss(
    candidates: Sequence[Match],
    best_prediction_iou: list[float],
    best_ground_truth_iou: list[float],
) -> None:
    # Every pair, not only the ones the greedy walk reaches: a neighbour counts whether or not
    # it was itself matched, or ever cleared the threshold.
    for candidate in candidates:
        pi, gi = candidate.prediction_index, candidate.ground_truth_index
        best_prediction_iou[pi] = max(best_prediction_iou[pi], candidate.iou)
        best_ground_truth_iou[gi] = max(best_ground_truth_iou[gi], candidate.iou)


def _candidates(
    predictions: Sequence[Box],
    ground_truth: Sequence[Box],
    prediction_indices: Sequence[int],
    ground_truth_indices: Sequence[int],
) -> list[Match]:
    pairs = [
        Match(
            prediction_index=pi,
            ground_truth_index=gi,
            iou=iou(predictions[pi]["bbox"], ground_truth[gi]["bbox"]),
        )
        for pi in prediction_indices
        for gi in ground_truth_indices
    ]

    pairs.sort(
        key=lambda pair: (-pair.iou, pair.prediction_index, pair.ground_truth_index)
    )
    return pairs


def _partition_by_cell(boxes: Sequence[Box]) -> dict[Cell, list[int]]:
    partitions: dict[Cell, list[int]] = {}
    for index, box in enumerate(boxes):
        partitions.setdefault(cell_of(box), []).append(index)
    return partitions


def _area(bbox: Sequence[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
