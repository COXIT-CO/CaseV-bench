from typing import Sequence

from ._aggregation import per_page, per_type, pool, tally
from ._matching import match
from ._metrics import rates
from ._objects import breakdown
from ._types import Box, ScoreResult
from ._validation import validate_ground_truth


def score(
    predictions: Sequence[Box],
    ground_truth: Sequence[Box],
    iou_threshold: float,
    include_objects: bool = False,
) -> ScoreResult:
    """Score localization predictions against ground truth.

    Both sides are ``{"object_type", "bbox", "page"}`` items sharing one coordinate space;
    ``object_type`` is a match key tested for equality only, with no taxonomy behind it, and
    there is no confidence anywhere. The result is a plain ``dict``, computed without touching
    the network, the filesystem, or the caller's lists::

        {"iou_threshold": ..., "counts": {"tp", "fp", "fn"}, "metrics": {...},
         "per_type": {<type>: {"counts", "metrics"}},
         "per_page": [{"page": ..., "counts", "metrics", "per_type"}],
         "objects": {"tp": [...], "fp": [...], "fn": [...]}}   # only if include_objects

    **Every level is micro-averaged** — pooled tallies, rates computed once, never an average
    of the rates below. ``per_page`` is a **list sorted by page, never a dict**: JSON object
    keys are always strings, and scores get persisted as JSON. ``per_type`` keys are exactly
    the types present on either side, with **no padding** to any taxonomy — the library holds
    none, so a caller wanting a row per label pads at its own call site.

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

    ``include_objects`` adds an ``objects`` key listing every true positive, false positive and
    false negative one by one; the key is **absent rather than ``None``** when not requested,
    and nothing else in the result changes. Each entry carries its **index in the caller's
    list**, which is the only thing that tells two byte-identical boxes apart.

    Every unmatched box also carries ``best_iou``: the highest IoU it reached against any
    same-``(page, object_type)`` box on the other side, **matched or not**, and ``0.0`` when
    there is none. It separates a box that landed just under the threshold from one the model
    invented — otherwise both are simply one FP, or one FN.

    **Validation is asymmetric.** An inverted or zero-area *ground-truth* box raises
    ``ValueError`` naming its index and the condition, before any matching: nothing can ever
    match it, so it would otherwise be a permanent false negative capping recall below 1.0
    with nothing in the numbers explaining why. The same geometry in a *prediction* never
    raises — zero area, IoU 0, a false positive — because one malformed box from an
    unreliable model must not stop a run.

    One trap. **Empty ground truth returns a well-formed F1 of 0.0, not ``None``** — that zero
    means "nothing to find", not "found nothing", so a caller that ranks results has to detect
    it rather than publish it beside real scores::

        if result["counts"]["tp"] + result["counts"]["fn"] == 0:
            ...  # no ground truth: unscored, not zero

    That test is true if and only if ``ground_truth`` was empty.
    """
    validate_ground_truth(ground_truth)
    matching = match(predictions, ground_truth, iou_threshold)
    cells = tally(predictions, ground_truth, matching)
    counts = pool(cells.values())
    result: ScoreResult = {
        "iou_threshold": iou_threshold,
        "counts": counts,
        "metrics": rates(counts),
        "per_type": per_type(cells),
        "per_page": per_page(cells),
    }
    if include_objects:
        result["objects"] = breakdown(predictions, ground_truth, matching)
    return result
