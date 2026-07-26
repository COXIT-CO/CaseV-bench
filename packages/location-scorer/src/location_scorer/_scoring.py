from typing import Sequence

from ._matching import match
from ._metrics import rates
from ._types import Box, Counts, ScoreResult


def score(
    predictions: Sequence[Box],
    ground_truth: Sequence[Box],
    iou_threshold: float,
    include_objects: bool = False,
) -> ScoreResult:
    """Score localization predictions against ground truth.

    Both sides are ``{"object_type", "bbox", "page"}`` items sharing one coordinate space;
    ``object_type`` is a match key tested for equality only, with no taxonomy behind it, and
    there is no confidence anywhere. The result is a plain ``dict`` of the echoed threshold,
    ``counts``, and micro-averaged ``metrics``, computed without touching the network, the
    filesystem, or the caller's lists.

    Matching partitions both sides by ``(page, object_type)`` and takes pairs best-IoU-first,
    one-to-one, while both boxes are still free; ties resolve to the lowest prediction index
    then the lowest ground-truth index. A pair matches when ``IoU >= iou_threshold``, except
    that **a pair with zero IoU never matches** — otherwise a threshold of 0.0 would credit a
    prediction against a ground-truth box it misses entirely.

    Greedy is deliberate: it is what COCO and essentially every published detection benchmark
    does, so these numbers stay comparable with the literature. Its known cost is that it can
    **under-count true positives relative to optimal assignment**, because a strong pair can
    strand a weaker one whose only remaining partner is below the threshold.

    ``iou_threshold`` has no default on purpose — the same version scores differently at
    different operating points, so the choice belongs in every call site and every diff.
    Predictions are never rejected: a malformed box is just a false positive, because one bad
    box from an unreliable model must not stop a run. ``include_objects`` is reserved for the
    per-object breakdown and adds no key yet.

    One trap. **Empty ground truth returns a well-formed F1 of 0.0, not ``None``** — that zero
    means "nothing to find", not "found nothing", so a caller that ranks results has to detect
    it rather than publish it beside real scores::

        if result["counts"]["tp"] + result["counts"]["fn"] == 0:
            ...  # no ground truth: unscored, not zero

    That test is true if and only if ``ground_truth`` was empty.
    """
    matching = match(predictions, ground_truth, iou_threshold)
    counts: Counts = {
        "tp": len(matching.matched),
        "fp": len(matching.unmatched_predictions),
        "fn": len(matching.unmatched_ground_truth),
    }
    return {
        "iou_threshold": iou_threshold,
        "counts": counts,
        "metrics": rates(counts),
    }
