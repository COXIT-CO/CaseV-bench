"""Pure location-scoring tests (spec: Testing Decisions — a metric returns the right
value for given inputs, not internal calls; ADR 0004). ``score_location`` is the
session-free seam: it takes predicted boxes and GT boxes keyed by page and returns
IoU@0.5 precision / recall / F1 — per label plus a micro-averaged Result aggregate — or
``None`` when the Drawing has no ground truth (unscored, not zero).

Boxes are matched **within a page and within a label**; the aggregate sums TP/FP/FN
across every page and label, so a perfect prediction scores 1.0 whatever the label mix.
"""

from services.scoring import LocationBox, score_location

CAB = "cabinets"
CTR = "countertops"


def _box(label, x_min, y_min, x_max, y_max):
    return LocationBox(label=label, x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)


def _by_label(score):
    return {ls.label: ls for ls in score.per_label}


def test_perfect_match_is_precision_recall_f1_one():
    # Every GT box has an identical prediction on the same page and label.
    gt = {
        1: [_box(CAB, 0.0, 0.0, 0.4, 0.4), _box(CTR, 0.5, 0.5, 0.7, 0.7)],
        2: [_box(CAB, 0.1, 0.1, 0.3, 0.3)],
    }
    predicted = {k: list(v) for k, v in gt.items()}

    score = score_location(predicted, gt)

    assert score is not None
    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.f1 == 1.0
    cab = _by_label(score)[CAB]
    assert (cab.tp, cab.fp, cab.fn) == (2, 0, 0)  # both pages' cabinet boxes matched


def test_zero_predictions_is_scored_zero_not_unscored():
    # GT exists, so the Result is scored — but with nothing predicted it earns nothing.
    gt = {1: [_box(CAB, 0.0, 0.0, 0.4, 0.4)]}

    score = score_location({}, gt)

    assert score is not None  # scored, distinct from unscored
    assert score.precision == 0.0
    assert score.recall == 0.0
    assert score.f1 == 0.0
    cab = _by_label(score)[CAB]
    assert (cab.tp, cab.fp, cab.fn) == (0, 0, 1)


def test_no_ground_truth_is_unscored_not_zero():
    predicted = {1: [_box(CAB, 0.0, 0.0, 0.4, 0.4)]}

    assert score_location(predicted, {}) is None


def test_iou_at_threshold_matches_just_above_and_not_below():
    # GT box is the unit square, so a prediction [0, 0, a, 1] has IoU == a exactly
    # (intersection a, union 1). Boxes must share page and label to be candidates.
    gt = {1: [_box(CAB, 0.0, 0.0, 1.0, 1.0)]}

    above = score_location({1: [_box(CAB, 0.0, 0.0, 0.6, 1.0)]}, gt)
    at = score_location({1: [_box(CAB, 0.0, 0.0, 0.5, 1.0)]}, gt)
    below = score_location({1: [_box(CAB, 0.0, 0.0, 0.4, 1.0)]}, gt)

    # IoU 0.6 and exactly 0.5 clear the >= 0.5 bar → a true positive.
    assert above.f1 == 1.0
    assert at.f1 == 1.0
    # IoU 0.4 misses → the prediction is a false positive and the GT a false negative.
    below_cab = _by_label(below)[CAB]
    assert (below_cab.tp, below_cab.fp, below_cab.fn) == (0, 1, 1)
    assert below.precision == 0.0
    assert below.recall == 0.0


def test_wrong_label_or_wrong_page_does_not_match():
    gt = {1: [_box(CAB, 0.0, 0.0, 0.4, 0.4)]}

    # Same box, wrong label → no match.
    wrong_label = score_location({1: [_box(CTR, 0.0, 0.0, 0.4, 0.4)]}, gt)
    assert wrong_label.f1 == 0.0
    # Same box and label, but on a different page → no match.
    wrong_page = score_location({2: [_box(CAB, 0.0, 0.0, 0.4, 0.4)]}, gt)
    assert wrong_page.f1 == 0.0


def test_partial_overlap_precision_and_recall_are_micro_averaged():
    # One matched box, one spurious prediction, one missed GT box → tp=1, fp=1, fn=1.
    gt = {
        1: [_box(CAB, 0.0, 0.0, 1.0, 1.0)],
        2: [_box(CAB, 0.0, 0.0, 1.0, 1.0)],  # this one is missed
    }
    predicted = {
        1: [
            _box(CAB, 0.0, 0.0, 1.0, 1.0),  # matches page-1 GT (tp)
            _box(CAB, 0.0, 0.0, 0.1, 0.1),  # no GT overlaps it (fp)
        ]
    }

    score = score_location(predicted, gt)

    assert score.precision == 0.5  # 1 tp / (1 tp + 1 fp)
    assert score.recall == 0.5  # 1 tp / (1 tp + 1 fn)
    assert score.f1 == 0.5  # 2*0.5*0.5 / (0.5 + 0.5)
