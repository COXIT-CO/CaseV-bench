from ._types import Counts, Metrics


def rates(counts: Counts) -> Metrics:
    precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
    recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
    return {
        "precision": precision,
        "recall": recall,
        "f1": _ratio(2 * precision * recall, precision + recall),
    }


def _ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
