"""Scoring + Leaderboard integration (spec: Testing Decisions — drive a whole Run
through the shared services against a temp SQLite DB, then assert what gets persisted
and ranked). With the adapter stubbed, a Run over two models plus entered GT yields a
Leaderboard ranked best-first; with no GT the Results render as unscored, not zero."""

from sqlmodel import select

from models.drawing import Drawing, Page
from models.prompt import Task
from models.score import Score
from services.counting_ground_truth import CountingGroundTruthService
from services.prompt import PromptService
from services.run import RunService
from services.scoring import LeaderboardMetric, ScoringService

ACCURATE = "anthropic/claude-sonnet-4.5"
SLOPPY = "openai/gpt-5-mini"

# GT for a 2-page drawing is the summed total; each page below contributes half.
GT = {"cabinets": 6, "countertops": 2, "elevations": 4, "elevation_callout": 0}
# Accurate model: exactly half the GT per page → perfect after summing 2 pages.
ACCURATE_JSON = (
    '{"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)
# Sloppy model: over-counts, so it has a nonzero total absolute error.
SLOPPY_JSON = (
    '{"cabinets": 5, "countertops": 1, "elevations": 2, "elevation_callout": 1}'
)


def _seed_drawing(session, n_pages: int = 2) -> Drawing:
    drawing = Drawing(name="sample")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    for page_number in range(1, n_pages + 1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=f"/tmp/page_{page_number}.png",
                width_px=100,
                height_px=100,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def _launch(session, stub_adapter, drawing):
    prompt = PromptService(session).create(Task.counting, "default", "count them")
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    return RunService(session, stub_adapter).launch(
        Task.counting, prompt.id, drawing.id, [ACCURATE, SLOPPY]
    )


def test_leaderboard_ranks_scored_results_best_first(session, stub_adapter):
    drawing = _seed_drawing(session)
    _launch(session, stub_adapter, drawing)
    CountingGroundTruthService(session).save(drawing.id, GT)

    rows = ScoringService(session).leaderboard(drawing.id)

    # Best-first: the accurate model (0 total error) ranks above the sloppy one.
    assert [r.model for r in rows] == [ACCURATE, SLOPPY]
    assert rows[0].total_absolute_error == 0
    assert rows[0].exact_match_count == 4
    # Sloppy: |10-6| cabinets + |2-4| elevation_callout... summed over 2 pages:
    # cabinets 10 vs 6 = 4, elevation_callout 2 vs 0 = 2 → total 6.
    assert rows[1].total_absolute_error == 6
    assert rows[1].scored is True

    # Rows carry the prompt-version × model identity the Leaderboard is keyed on.
    assert rows[0].prompt_family == "default"
    assert rows[0].prompt_version == 1


def test_leaderboard_can_rank_by_exact_match_count(session, stub_adapter):
    drawing = _seed_drawing(session)
    _launch(session, stub_adapter, drawing)
    CountingGroundTruthService(session).save(drawing.id, GT)

    rows = ScoringService(session).leaderboard(
        drawing.id, metric=LeaderboardMetric.exact_match_count
    )

    # Accurate model matches all 4 labels; sloppy matches 2 → accurate still first.
    assert rows[0].model == ACCURATE
    assert rows[0].exact_match_count == 4
    assert rows[1].exact_match_count == 2


def test_result_is_unscored_without_ground_truth(session, stub_adapter):
    drawing = _seed_drawing(session)
    _launch(session, stub_adapter, drawing)

    rows = ScoringService(session).leaderboard(drawing.id)

    # No GT entered: every Result is unscored (distinct from a zero score), and no Score
    # rows are persisted.
    assert all(r.scored is False for r in rows)
    assert all(r.total_absolute_error is None for r in rows)
    assert session.exec(select(Score)).all() == []


def test_score_is_recomputed_when_ground_truth_changes(session, stub_adapter):
    """A Score is a recompute against current GT, not a frozen value (ADR 0004): entering
    then correcting GT re-ranks without re-running the model."""
    drawing = _seed_drawing(session)
    _launch(session, stub_adapter, drawing)
    gt_service = CountingGroundTruthService(session)

    gt_service.save(drawing.id, GT)
    accurate_row = next(
        r
        for r in ScoringService(session).leaderboard(drawing.id)
        if r.model == ACCURATE
    )
    assert accurate_row.total_absolute_error == 0

    # Correct the GT so the previously-perfect model now has an error.
    gt_service.save(drawing.id, {**GT, "cabinets": 8})
    accurate_row = next(
        r
        for r in ScoringService(session).leaderboard(drawing.id)
        if r.model == ACCURATE
    )
    assert accurate_row.total_absolute_error == 2  # |6-8|
