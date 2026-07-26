from typing import Sequence, TypedDict


class Box(TypedDict):
    object_type: str
    bbox: Sequence[float]  # [x_min, y_min, x_max, y_max]
    page: int


class Counts(TypedDict):
    tp: int
    fp: int
    fn: int


class Metrics(TypedDict):
    precision: float
    recall: float
    f1: float


class ScoreResult(TypedDict):
    iou_threshold: float
    counts: Counts
    metrics: Metrics
