from typing import NamedTuple, NotRequired, Sequence, TypedDict


class Box(TypedDict):
    object_type: str
    bbox: Sequence[float]  # [x_min, y_min, x_max, y_max]
    page: int


class Cell(NamedTuple):
    page: int
    object_type: str


def cell_of(box: Box) -> Cell:
    return Cell(page=box["page"], object_type=box["object_type"])


class Counts(TypedDict):
    tp: int
    fp: int
    fn: int


class Metrics(TypedDict):
    precision: float
    recall: float
    f1: float


class TypeScore(TypedDict):
    counts: Counts
    metrics: Metrics


class PageScore(TypedDict):
    page: int
    counts: Counts
    metrics: Metrics
    per_type: dict[str, TypeScore]


class TruePositive(TypedDict):
    page: int
    object_type: str
    prediction_index: int
    ground_truth_index: int
    prediction: list[float]
    ground_truth: list[float]
    iou: float


class FalsePositive(TypedDict):
    page: int
    object_type: str
    prediction_index: int
    prediction: list[float]
    best_iou: float


class FalseNegative(TypedDict):
    page: int
    object_type: str
    ground_truth_index: int
    ground_truth: list[float]
    best_iou: float


class ObjectsBreakdown(TypedDict):
    tp: list[TruePositive]
    fp: list[FalsePositive]
    fn: list[FalseNegative]


class ScoreResult(TypedDict):
    iou_threshold: float
    counts: Counts
    metrics: Metrics
    per_type: dict[str, TypeScore]
    per_page: list[PageScore]  # a list, never a page-keyed dict — see `score()`
    objects: NotRequired[ObjectsBreakdown]
