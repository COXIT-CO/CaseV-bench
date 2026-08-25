from typing import Iterable, Literal, Mapping, Sequence

from ._matching import Matching
from ._metrics import rates
from ._types import Box, Cell, Counts, PageScore, TypeScore, cell_of


def tally(
    predictions: Sequence[Box],
    ground_truth: Sequence[Box],
    matching: Matching,
) -> dict[Cell, Counts]:
    cells: dict[Cell, Counts] = {}
    for match in matching.matched:
        _increment(cells, cell_of(predictions[match.prediction_index]), "tp")
    for index in matching.unmatched_predictions:
        _increment(cells, cell_of(predictions[index]), "fp")
    for index in matching.unmatched_ground_truth:
        _increment(cells, cell_of(ground_truth[index]), "fn")
    return cells


def per_type(cells: Mapping[Cell, Counts]) -> dict[str, TypeScore]:
    pooled: dict[str, list[Counts]] = {}
    for cell, counts in cells.items():
        pooled.setdefault(cell.object_type, []).append(counts)
    return {
        object_type: _type_score(pool(type_cells))
        for object_type, type_cells in sorted(pooled.items())
    }


def per_page(cells: Mapping[Cell, Counts]) -> list[PageScore]:
    by_page: dict[int, dict[Cell, Counts]] = {}
    for cell, counts in cells.items():
        by_page.setdefault(cell.page, {})[cell] = counts
    return [
        _page_score(page, page_cells) for page, page_cells in sorted(by_page.items())
    ]


def _page_score(page: int, cells: Mapping[Cell, Counts]) -> PageScore:
    counts = pool(cells.values())
    return {
        "page": page,
        "counts": counts,
        "metrics": rates(counts),
        "per_type": per_type(cells),
    }


def _type_score(counts: Counts) -> TypeScore:
    return {"counts": counts, "metrics": rates(counts)}


def pool(counts: Iterable[Counts]) -> Counts:
    pooled: Counts = {"tp": 0, "fp": 0, "fn": 0}
    for one in counts:
        pooled["tp"] += one["tp"]
        pooled["fp"] += one["fp"]
        pooled["fn"] += one["fn"]
    return pooled


def _increment(
    cells: dict[Cell, Counts], cell: Cell, outcome: Literal["tp", "fp", "fn"]
) -> None:
    cells.setdefault(cell, {"tp": 0, "fp": 0, "fn": 0})[outcome] += 1
