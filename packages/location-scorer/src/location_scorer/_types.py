from typing import NamedTuple, Sequence, TypedDict


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


class ScoreResult(TypedDict):
    iou_threshold: float
    counts: Counts
    metrics: Metrics
    per_type: dict[str, TypeScore]
    per_page: list[PageScore]  # a list, never a page-keyed dict — see `score()`
