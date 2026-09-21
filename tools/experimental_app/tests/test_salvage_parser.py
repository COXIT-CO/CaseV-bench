"""Robust salvaging JSON parser (ADR 0019, ticket 03) — the pure helper, unit-tested
directly with a table of malformed inputs → expected salvage (spec: Testing Decisions).

``salvage_json`` recovers a Python structure from whatever a model returns and reports
whether the recovery was *complete* (fully valid JSON, nothing dropped → the caller
scores it ``ok``) or a partial *salvage* (a truncated/partly-corrupt array reduced to its
intact elements → the caller keeps it ``error`` but renders what survived). Prose/fences,
trailing commas and single quotes are tolerated **without** losing data, so they count as
complete; only dropping elements (or failing outright) is non-complete.
"""

import json

import pytest

from core.utils import salvage_json

CABINET = {
    "label": "cabinet",
    "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4},
}
COUNTER = {
    "label": "countertop",
    "bounding_box": {"x_min": 0.5, "y_min": 0.5, "x_max": 0.6, "y_max": 0.7},
}


# (name, raw content, expected value, expected complete)
COMPLETE_CASES = [
    ("clean object", '{"cabinet": 3}', {"cabinet": 3}, True),
    ("clean array", json.dumps([CABINET, COUNTER]), [CABINET, COUNTER], True),
    ("fenced object", '```json\n{"cabinet": 3}\n```', {"cabinet": 3}, True),
    (
        "prose-wrapped object",
        'Sure! Here is the count:\n{"cabinet": 3}\nHope that helps.',
        {"cabinet": 3},
        True,
    ),
    (
        "reasoning then array",
        "I count two boxes. " + json.dumps([CABINET, COUNTER]) + " Done.",
        [CABINET, COUNTER],
        True,
    ),
    ("trailing comma object", '{"cabinet": 3,}', {"cabinet": 3}, True),
    ("trailing comma array", "[1, 2, 3,]", [1, 2, 3], True),
    ("single-quoted object", "{'cabinet': 3}", {"cabinet": 3}, True),
    (
        "brace inside string",
        '{"note": "a {curly} brace", "cabinet": 3}',
        {"note": "a {curly} brace", "cabinet": 3},
        True,
    ),
    ("empty array", "[]", [], True),
]


@pytest.mark.parametrize(
    "raw,expected,complete",
    [(c[1], c[2], c[3]) for c in COMPLETE_CASES],
    ids=[c[0] for c in COMPLETE_CASES],
)
def test_complete_recoveries(raw, expected, complete):
    result = salvage_json(raw)
    assert result.value == expected
    assert result.complete is complete
    assert result.error is None


def test_truncated_array_yields_intact_elements():
    # Two whole elements, then the array is cut off mid-third.
    raw = "[" + json.dumps(CABINET) + ", " + json.dumps(COUNTER) + ', {"label": "elev'
    result = salvage_json(raw)
    assert result.value == [CABINET, COUNTER]
    assert result.complete is False
    assert result.error is not None


def test_truncated_array_with_trailing_corruption():
    # First element intact, second element malformed (kept out of the salvage).
    raw = "[" + json.dumps(CABINET) + ', {"label": "countertop", "bounding_box": {x'
    result = salvage_json(raw)
    assert result.value == [CABINET]
    assert result.complete is False


def test_truncated_before_any_complete_element_recovers_nothing():
    raw = '[{"label": "cabinet", "bounding_box": {"x_min": 0.1'
    result = salvage_json(raw)
    assert result.value is None
    assert result.complete is False
    assert result.error is not None


@pytest.mark.parametrize(
    "raw",
    ["not json at all", "", "   ", "The model refused to answer."],
    ids=["prose", "empty", "whitespace", "refusal"],
)
def test_total_garbage_recovers_nothing(raw):
    result = salvage_json(raw)
    assert result.value is None
    assert result.complete is False
    assert result.error is not None


def test_none_content_is_reported_not_raised():
    result = salvage_json(None)
    assert result.value is None
    assert result.complete is False
    assert result.error is not None
