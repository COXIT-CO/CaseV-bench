"""Location scoring + Leaderboard integration (spec: Testing Decisions — drive a whole
location Run through the shared services against a temp SQLite DB, then assert what gets
persisted and ranked; ADR 0004, ticket 11). With the adapter stubbed to canned boxes, a
Run over two models plus imported COCO GT yields a Leaderboard ranked best-first by F1;
with no GT the Results render as unscored, not zero."""

import json
from pathlib import Path

from PIL import Image
from sqlmodel import select

from models.drawing import Drawing, Page
from models.prompt import Task
from models.score import Score
from services.location_ground_truth import LocationGroundTruthService
from services.prompt import PromptService
from services.run import RunService
from services.scoring import LocationLeaderboardMetric, ScoringService

ACCURATE = "anthropic/claude-sonnet-4.5"
SLOPPY = "openai/gpt-5-mini"

# GT for the (single) page: one cabinet, one countertop.
GT_CABINET = {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4}
GT_COUNTERTOP = {"x_min": 0.5, "y_min": 0.5, "x_max": 0.7, "y_max": 0.7}

# Accurate model returns both GT boxes exactly → precision = recall = F1 = 1.0.
ACCURATE_JSON = json.dumps(
    [
        {"label": "cabinets", "bounding_box": GT_CABINET},
        {"label": "countertops", "bounding_box": GT_COUNTERTOP},
    ]
)
# Sloppy model finds the cabinet but misses the countertop and hallucinates a third
# box → tp=1, fp=1, fn=1 → precision = recall = F1 = 0.5.
SLOPPY_JSON = json.dumps(
    [
        {"label": "cabinets", "bounding_box": GT_CABINET},
        {
            "label": "cabinets",
            "bounding_box": {"x_min": 0.8, "y_min": 0.8, "x_max": 0.9, "y_max": 0.9},
        },
    ]
)


def _seed_drawing(session, tmp_path: Path) -> Drawing:
    """A 1-page Drawing whose Page points at a real PNG so overlay rendering has an
    image to draw on. Page dims are 100×100 so COCO pixel boxes normalize cleanly."""
    drawing = Drawing(name="sample")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    image_path = tmp_path / "page_0001.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    session.add(
        Page(
            drawing_id=drawing.id,
            page_number=1,
            image_path=str(image_path),
            width_px=100,
            height_px=100,
        )
    )
    session.commit()
    session.refresh(drawing)
    return drawing


def _import_gt(session, drawing) -> None:
    """Import the two GT boxes via the COCO importer (ticket 10) so the whole scored
    path — import → score → rank — is exercised, not just a hand-built Score."""
    coco = {
        "images": [
            {"id": 1, "file_name": "page_0001.png", "width": 100, "height": 100}
        ],
        "categories": [
            {"id": 1, "name": "cabinets"},
            {"id": 2, "name": "countertops"},
        ],
        # COCO bbox is [x, y, w, h] in pixels; page is 100×100 so /100 gives the norms.
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 30, 30]},
            {"id": 2, "image_id": 1, "category_id": 2, "bbox": [50, 50, 20, 20]},
        ],
    }
    LocationGroundTruthService(session).import_coco(drawing.id, coco)


def _launch(session, stub_adapter, drawing, overlay_root):
    prompt = PromptService(session).create(Task.location, "default", "find them")
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    return RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [ACCURATE, SLOPPY]
    )


def test_location_leaderboard_ranks_scored_results_best_first(
    session, stub_adapter, tmp_path
):
    drawing = _seed_drawing(session, tmp_path)
    _launch(session, stub_adapter, drawing, tmp_path / "overlays")
    _import_gt(session, drawing)

    rows = ScoringService(session).location_leaderboard(drawing.id)

    # Best-first by F1: the accurate model (F1 1.0) ranks above the sloppy one (0.5).
    assert [r.model for r in rows] == [ACCURATE, SLOPPY]
    assert rows[0].precision == 1.0
    assert rows[0].recall == 1.0
    assert rows[0].f1 == 1.0
    assert rows[1].precision == 0.5  # 1 tp / (1 tp + 1 fp)
    assert rows[1].recall == 0.5  # 1 tp / (1 tp + 1 fn)
    assert rows[1].f1 == 0.5
    assert rows[1].scored is True

    # Rows carry the prompt-version × model identity the Leaderboard is keyed on.
    assert rows[0].prompt_family == "default"
    assert rows[0].prompt_version == 1


def test_location_leaderboard_can_rank_by_recall(session, stub_adapter, tmp_path):
    drawing = _seed_drawing(session, tmp_path)
    _launch(session, stub_adapter, drawing, tmp_path / "overlays")
    _import_gt(session, drawing)

    rows = ScoringService(session).location_leaderboard(
        drawing.id, metric=LocationLeaderboardMetric.recall
    )

    # Accurate recall 1.0 still beats sloppy recall 0.5.
    assert rows[0].model == ACCURATE
    assert rows[0].recall == 1.0
    assert rows[1].recall == 0.5


def test_location_result_is_unscored_without_ground_truth(
    session, stub_adapter, tmp_path
):
    drawing = _seed_drawing(session, tmp_path)
    _launch(session, stub_adapter, drawing, tmp_path / "overlays")

    rows = ScoringService(session).location_leaderboard(drawing.id)

    # No GT imported: every Result is unscored (distinct from a zero score), and no
    # Score rows are persisted.
    assert all(r.scored is False for r in rows)
    assert all(r.f1 is None for r in rows)
    assert session.exec(select(Score)).all() == []


def test_location_score_is_recomputed_when_ground_truth_changes(
    session, stub_adapter, tmp_path
):
    """A Score is a recompute against current GT, not a frozen value (ADR 0004):
    importing then re-importing different GT re-ranks without re-running the model."""
    drawing = _seed_drawing(session, tmp_path)
    _launch(session, stub_adapter, drawing, tmp_path / "overlays")
    gt_service = LocationGroundTruthService(session)

    _import_gt(session, drawing)
    accurate_row = next(
        r
        for r in ScoringService(session).location_leaderboard(drawing.id)
        if r.model == ACCURATE
    )
    assert accurate_row.f1 == 1.0

    # Re-import GT with only the cabinet moved out from under the accurate prediction:
    # a single GT box the model no longer matches → recall drops to 0.
    gt_service.import_coco(
        drawing.id,
        {
            "images": [
                {"id": 1, "file_name": "page_0001.png", "width": 100, "height": 100}
            ],
            "categories": [{"id": 1, "name": "cabinets"}],
            "annotations": [
                {"id": 1, "image_id": 1, "category_id": 1, "bbox": [80, 80, 15, 15]},
            ],
        },
    )
    accurate_row = next(
        r
        for r in ScoringService(session).location_leaderboard(drawing.id)
        if r.model == ACCURATE
    )
    # The accurate model's two boxes now match nothing; the lone GT box is missed.
    assert accurate_row.recall == 0.0
    assert accurate_row.f1 == 0.0
