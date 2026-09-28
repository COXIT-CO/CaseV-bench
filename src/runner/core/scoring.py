from collections import Counter
from collections.abc import Sequence
from importlib import metadata
from typing import Any, TypedDict

from location_scorer import score as score_boxes

IOU_SWEEP: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(1, 19))
CANONICAL_IOU_THRESHOLD = 0.5


class Box(TypedDict):
    object_type: str
    bbox: Sequence[float]
    page: int


class DrawingScore(TypedDict):
    drawing: str
    canonical_iou_threshold: float
    unscored: bool
    scorer_version: str
    scores: dict[str, Any]


class RunThresholdScore(TypedDict):
    counts: dict[str, int]
    metrics: dict[str, float]
    per_type: dict[str, dict[str, Any]]


class RunScore(TypedDict):
    canonical_iou_threshold: float
    scorer_version: str
    drawings: list[str]
    scores: dict[str, RunThresholdScore]


class ScorerWrapper:
    """Scores predictions against ground truth via the location-scorer library."""

    @staticmethod
    def scorer_version() -> str:
        return metadata.version("location-scorer")

    @staticmethod
    def _threshold_key(threshold: float) -> str:
        return f"{threshold:.2f}"

    @staticmethod
    def _empty_counts() -> Counter[str]:
        return Counter({"tp": 0, "fp": 0, "fn": 0})

    @staticmethod
    def _rates(counts: dict[str, int]) -> dict[str, float]:
        def ratio(numerator: float, denominator: float) -> float:
            return numerator / denominator if denominator else 0.0

        precision = ratio(counts["tp"], counts["tp"] + counts["fp"])
        recall = ratio(counts["tp"], counts["tp"] + counts["fn"])
        return {
            "precision": precision,
            "recall": recall,
            "f1": ratio(2 * precision * recall, precision + recall),
        }

    def score_drawing(
        self,
        drawing_name: str,
        predictions: list[Box],
        ground_truth: list[Box],
        iou_sweep: Sequence[float] = IOU_SWEEP,
        canonical_iou_threshold: float = CANONICAL_IOU_THRESHOLD,
    ) -> DrawingScore:
        scores = {
            self._threshold_key(threshold): score_boxes(predictions, ground_truth, threshold)
            for threshold in iou_sweep
        }
        return {
            "drawing": drawing_name,
            "canonical_iou_threshold": canonical_iou_threshold,
            "unscored": len(ground_truth) == 0,
            "scorer_version": self.scorer_version(),
            "scores": scores,
        }

    def aggregate_run_score(self, drawing_scores: Sequence[DrawingScore]) -> RunScore:
        scores: dict[str, RunThresholdScore] = {}
        for key in sorted({key for ds in drawing_scores for key in ds["scores"]}):
            counts = self._empty_counts()
            per_type_counts: dict[str, Counter[str]] = {}
            for ds in drawing_scores:
                result = ds["scores"][key]
                counts.update(result["counts"])
                for label, type_score in result["per_type"].items():
                    per_type_counts.setdefault(label, self._empty_counts()).update(
                        type_score["counts"]
                    )
            scores[key] = {
                "counts": counts,
                "metrics": self._rates(counts),
                "per_type": {
                    label: {"counts": type_counts, "metrics": self._rates(type_counts)}
                    for label, type_counts in sorted(per_type_counts.items())
                },
            }

        return {
            "canonical_iou_threshold": drawing_scores[0]["canonical_iou_threshold"],
            "scorer_version": drawing_scores[0]["scorer_version"],
            "drawings": [ds["drawing"] for ds in drawing_scores],
            "scores": scores,
        }
