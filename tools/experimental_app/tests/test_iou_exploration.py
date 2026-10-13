"""The read-time IoU knob: re-score a board or a Result at an arbitrary operating point
without disturbing the canonical numbers.

Recompute-on-read (ADR 0004) is what makes this cheap — no re-run, no model calls — and it
is also what makes it dangerous, since the canonical read path *persists* what it computes.
So the property under test is not really "0.3 ranks differently"; it is **that an
exploratory read writes nothing**. A regression there would silently rewrite every published
0.5 rate into a 0.3 one, with nothing in the data saying it happened.

The fixture geometry is chosen so the threshold alone decides the outcome: the prediction
fully contains the single GT box at 2.5× its area, so IoU is exactly 0.4 — a match at 0.3,
a miss at CaseV's canonical 0.5.
"""

import json
import time

import pytest
from conftest import seed_location_drawing_id
from sqlmodel import Session, select

from core.models.score import Score
from core.services.scoring import LOCATION_IOU_THRESHOLD

MODEL = "anthropic/claude-sonnet-4.5"

# GT is x/y 0.1–0.4 (area 0.09). A concentric box of area 0.225 contains it entirely, so
# intersection = 0.09, union = 0.225, IoU = 0.4 exactly.
_HALF = 0.2371708245126285
LOOSE_BOX_JSON = json.dumps(
    [
        {
            "label": "cabinet",
            "bounding_box": {
                "x_min": 0.25 - _HALF,
                "y_min": 0.25 - _HALF,
                "x_max": 0.25 + _HALF,
                "y_max": 0.25 + _HALF,
            },
        }
    ]
)


@pytest.fixture
def loose_board(client, engine, stub_adapter, location_prompt, seed_location_gt):
    """A one-Drawing, one-model board whose only prediction lands at IoU 0.4."""
    drawing_id = seed_location_drawing_id(engine)
    stub_adapter.responses = {MODEL: LOOSE_BOX_JSON}

    launched = client.post(
        "/api/runs",
        json={
            "prompt_id": location_prompt.id,
            "drawing_id": drawing_id,
            "models": [MODEL],
        },
    )
    run_id = launched.json()["id"]
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if client.get(f"/api/runs/{run_id}/status").json()["status"] == "done":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("run did not finish in time")

    with Session(engine) as session:
        seed_location_gt(session, drawing_id)
    return drawing_id


def _scores(engine) -> dict[int, tuple[float, float, float]]:
    with Session(engine) as session:
        return {
            s.result_id: (s.precision, s.recall, s.f1)
            for s in session.exec(select(Score)).all()
        }


def test_canonical_board_misses_the_loose_box(client, loose_board):
    """At 0.5 the 0.4-IoU box is a false positive, so the Configuration scores zero."""
    body = client.get(f"/api/leaderboard?drawing_id={loose_board}").json()

    assert body["iou_threshold"] == LOCATION_IOU_THRESHOLD
    assert body["canonical_iou"] is True
    assert body["canonical_iou_threshold"] == LOCATION_IOU_THRESHOLD
    assert body["rows"][0]["scored"] is True
    assert body["rows"][0]["f1"] == 0.0


def test_exploring_at_a_lower_threshold_credits_the_box(client, loose_board):
    """The same stored prediction, re-scored at 0.3, becomes a true positive."""
    body = client.get(
        f"/api/leaderboard?drawing_id={loose_board}&iou_threshold=0.3"
    ).json()

    assert body["iou_threshold"] == 0.3
    assert body["canonical_iou"] is False
    # Still reported, so the SPA can say what the board is deviating *from*.
    assert body["canonical_iou_threshold"] == LOCATION_IOU_THRESHOLD
    assert body["rows"][0]["f1"] == 1.0


def test_exploring_the_board_persists_nothing(client, engine, loose_board):
    """The invariant. Browsing at another operating point must leave the Score table byte
    for byte as the canonical read left it — otherwise the published numbers silently
    become whatever threshold someone last looked at."""
    client.get(f"/api/leaderboard?drawing_id={loose_board}")
    canonical = _scores(engine)
    assert (
        canonical
    ), "the canonical read should have persisted a Score to compare against"

    for threshold in (0.1, 0.3, 0.9):
        client.get(
            f"/api/leaderboard?drawing_id={loose_board}&iou_threshold={threshold}"
        )
        assert (
            _scores(engine) == canonical
        ), f"browsing at {threshold} rewrote a Score row"


def test_exploring_a_result_persists_nothing(client, engine, loose_board):
    """Same invariant on the drill-down, which has its own read path."""
    result_id = client.get(f"/api/leaderboard?drawing_id={loose_board}").json()["rows"][
        0
    ]["result_id"]
    client.get(f"/api/results/{result_id}")
    canonical = _scores(engine)

    explored = client.get(f"/api/results/{result_id}?iou_threshold=0.3").json()
    assert explored["iou_threshold"] == 0.3
    assert explored["canonical_iou"] is False
    assert explored["location_score"]["f1"] == 1.0
    # The per-label breakdown travels with the exploratory rates, not the stored ones.
    cabinet = next(
        row
        for row in explored["location_score"]["per_label"]
        if row["label"] == "cabinet"
    )
    assert (cabinet["tp"], cabinet["fp"], cabinet["fn"]) == (1, 0, 0)

    assert _scores(engine) == canonical


def test_result_detail_echoes_the_canonical_point_by_default(client, loose_board):
    result_id = client.get(f"/api/leaderboard?drawing_id={loose_board}").json()["rows"][
        0
    ]["result_id"]
    body = client.get(f"/api/results/{result_id}").json()

    assert body["iou_threshold"] == LOCATION_IOU_THRESHOLD
    assert body["canonical_iou"] is True
    assert body["location_score"]["f1"] == 0.0


@pytest.mark.parametrize("bad", ["0", "-0.2", "1.5", "abc"])
@pytest.mark.parametrize("path", ["/api/leaderboard", "/api/results/1"])
def test_out_of_range_thresholds_are_rejected(client, path, bad):
    """A threshold outside (0, 1] is nonsense rather than a different operating point, so it
    is a 422 instead of quietly clamping to something the response would then echo."""
    assert client.get(f"{path}?iou_threshold={bad}").status_code == 422
