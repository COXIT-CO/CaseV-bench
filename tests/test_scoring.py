"""Pure counting-scoring tests (spec: Testing Decisions — a metric returns the right
value for given inputs, not internal calls). ``score_counting`` is the session-free
seam: it takes summed-across-pages predicted totals and GT totals and returns per-label
absolute error + exact-match, or ``None`` when GT is missing (unscored, not zero)."""

from models.results import OBJECT_LABELS
from services.scoring import score_counting

GT = {"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}


def _by_label(score):
    return {ls.label: ls for ls in score.per_label}


def test_perfect_match_is_zero_error_and_all_exact():
    score = score_counting(dict(GT), GT)

    assert score is not None
    assert score.total_absolute_error == 0
    assert score.exact_match_count == len(OBJECT_LABELS)
    for ls in score.per_label:
        assert ls.absolute_error == 0
        assert ls.exact_match is True


def test_off_by_n_reports_absolute_error_per_label():
    predicted = {
        "cabinets": 5,
        "countertops": 1,
        "elevations": 0,
        "elevation_callout": 4,
    }
    score = score_counting(predicted, GT)

    by_label = _by_label(score)
    # |5-3| + |1-1| + |0-2| + |4-0| = 2 + 0 + 2 + 4
    assert by_label["cabinets"].absolute_error == 2
    assert by_label["cabinets"].exact_match is False
    assert by_label["countertops"].absolute_error == 0
    assert by_label["countertops"].exact_match is True
    assert by_label["elevations"].absolute_error == 2
    assert by_label["elevation_callout"].absolute_error == 4
    assert score.total_absolute_error == 8
    assert score.exact_match_count == 1


def test_missing_gt_is_unscored_not_zero():
    assert score_counting(dict(GT), {}) is None


def test_missing_predicted_label_counts_as_zero():
    # A label the model never predicted is a full miss against its GT total, not skipped.
    score = score_counting({}, GT)

    by_label = _by_label(score)
    assert by_label["cabinets"].predicted == 0
    assert by_label["cabinets"].absolute_error == 3
    assert score.total_absolute_error == sum(GT.values())
    # elevation_callout GT is 0, so predicting 0 is an exact match.
    assert by_label["elevation_callout"].exact_match is True
    assert score.exact_match_count == 1
