"""Delete-a-Drawing service seam (ticket 08, ADR-0016).

Deleting a Drawing must cascade to everything derived from it — its Pages, both kinds of
ground truth, its on-disk page images, and (reusing the Run-deletion machinery) every Run
and Result that used it, along with their Predictions, Scores, and overlay files — while
leaving every unrelated Drawing and Run untouched. A real location Run is launched through
the shared service (adapter stubbed) so the rows and on-disk artifacts exist exactly as
production writes them, then the Drawing is deleted.
"""

import json
from pathlib import Path

import pytest
from PIL import Image
from sqlmodel import select

from core.models.counting_ground_truth import CountingGroundTruth
from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.prompt import Task
from core.models.run import Prediction, Result, Run
from core.models.score import Score
from core.services.drawing import DrawingService
from core.services.pdf_processing import page_image_filename
from core.services.prompt import PromptService
from core.services.run import RunService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"

BOXES_JSON = json.dumps(
    [
        {
            "label": "cabinet",
            "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4},
        }
    ]
)


def _seed_drawing(session, cache_root: Path, name: str, n_pages: int = 2) -> Drawing:
    """A Drawing whose Pages point at real PNGs under ``cache_root/<id>/`` (as ingestion
    lays them out), plus both kinds of ground truth, so a delete has files and GT to clear.
    """
    drawing = Drawing(name=name)
    session.add(drawing)
    session.commit()
    session.refresh(drawing)

    page_dir = cache_root / str(drawing.id)
    page_dir.mkdir(parents=True, exist_ok=True)
    for page_number in range(1, n_pages + 1):
        image_path = page_dir / page_image_filename(page_number)
        Image.new("RGB", (100, 100), "white").save(image_path)
        page = Page(
            drawing_id=drawing.id,
            page_number=page_number,
            image_path=str(image_path),
            width_px=100,
            height_px=100,
        )
        session.add(page)
        session.commit()
        session.refresh(page)
        session.add(
            LocationGroundTruth(
                page_id=page.id,
                label="cabinet",
                x_min=0.1,
                y_min=0.1,
                x_max=0.4,
                y_max=0.4,
            )
        )
    session.add(CountingGroundTruth(drawing_id=drawing.id, label="cabinet", total=3))
    session.commit()
    session.refresh(drawing)
    return drawing


def _launch_location_run(session, stub_adapter, overlay_root, drawing):
    """Launch a 2-model, 2-page location Run against ``drawing`` so it has Results,
    Predictions, and rendered overlays."""
    prompt = PromptService(session).create(Task.location, drawing.name, "find them")
    stub_adapter.responses = {SONNET: BOXES_JSON, GPT: BOXES_JSON}
    return RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [SONNET, GPT]
    )


def test_delete_drawing_cascades_rows_files_and_returns_counts(
    session, stub_adapter, tmp_path
):
    cache_root = tmp_path / "drawings"
    overlay_root = tmp_path / "overlays"
    drawing = _seed_drawing(session, cache_root, "target")
    run = _launch_location_run(session, stub_adapter, overlay_root, drawing)

    page_ids = [p.id for p in drawing.pages]
    result_ids = [r.id for r in run.results]
    prediction_ids = session.exec(
        select(Prediction.id).where(Prediction.result_id.in_(result_ids))
    ).all()
    overlay_dirs = [overlay_root / str(rid) for rid in result_ids]
    page_image_dir = cache_root / str(drawing.id)
    # The Run rendered its pages on demand, caching a per-(dpi, downsample) variant under the
    # drawing dir (ticket 05); the delete-cascade must sweep these render caches too.
    render_caches = [d for d in page_image_dir.glob("render_*") if d.is_dir()]
    # A Score attaches to a Result and must go with the cascade.
    session.add(Score(result_id=result_ids[0], per_label_json="[]"))
    session.commit()

    # Preconditions: rows and on-disk artifacts are all present.
    assert len(page_ids) == 2
    assert len(result_ids) == 2
    assert len(prediction_ids) == 4
    assert all(d.is_dir() and any(d.iterdir()) for d in overlay_dirs)
    assert page_image_dir.is_dir() and any(page_image_dir.iterdir())
    assert render_caches and all(any(c.iterdir()) for c in render_caches)

    counts = DrawingService(
        session, cache_root=cache_root, overlay_root=overlay_root
    ).delete_drawing(drawing.id)

    # The collateral is the Runs + Results the cascade removed.
    assert counts.runs == 1
    assert counts.results == 2

    # The Drawing and everything derived from it is gone.
    assert session.get(Drawing, drawing.id) is None
    assert session.exec(select(Page).where(Page.id.in_(page_ids))).all() == []
    assert (
        session.exec(
            select(CountingGroundTruth).where(
                CountingGroundTruth.drawing_id == drawing.id
            )
        ).all()
        == []
    )
    assert (
        session.exec(
            select(LocationGroundTruth).where(LocationGroundTruth.page_id.in_(page_ids))
        ).all()
        == []
    )
    # Its Runs / Results / Predictions / Scores are gone (leave the Leaderboard).
    assert session.get(Run, run.id) is None
    assert session.exec(select(Result).where(Result.id.in_(result_ids))).all() == []
    assert (
        session.exec(select(Prediction).where(Prediction.id.in_(prediction_ids))).all()
        == []
    )
    assert (
        session.exec(select(Score).where(Score.result_id.in_(result_ids))).all() == []
    )
    # On-disk artifacts — the page images, the per-run render caches, and the overlay dirs —
    # are all cleaned up.
    assert not page_image_dir.exists()
    assert not any(c.exists() for c in render_caches)
    assert not any(d.exists() for d in overlay_dirs)


def test_delete_drawing_leaves_unrelated_data_untouched(
    session, stub_adapter, tmp_path
):
    cache_root = tmp_path / "drawings"
    overlay_root = tmp_path / "overlays"
    keep = _seed_drawing(session, cache_root, "keep")
    drop = _seed_drawing(session, cache_root, "drop")
    keep_run = _launch_location_run(session, stub_adapter, overlay_root, keep)
    drop_run = _launch_location_run(session, stub_adapter, overlay_root, drop)

    keep_page_ids = [p.id for p in keep.pages]
    keep_result_ids = [r.id for r in keep_run.results]
    keep_overlay_dirs = [overlay_root / str(rid) for rid in keep_result_ids]

    DrawingService(
        session, cache_root=cache_root, overlay_root=overlay_root
    ).delete_drawing(drop.id)

    # The other Drawing keeps its rows, ground truth, run, and on-disk files.
    assert session.get(Drawing, keep.id) is not None
    assert len(session.exec(select(Page).where(Page.id.in_(keep_page_ids))).all()) == 2
    assert session.exec(
        select(CountingGroundTruth).where(CountingGroundTruth.drawing_id == keep.id)
    ).all()
    assert session.exec(
        select(LocationGroundTruth).where(
            LocationGroundTruth.page_id.in_(keep_page_ids)
        )
    ).all()
    assert session.get(Run, keep_run.id) is not None
    assert (cache_root / str(keep.id)).is_dir()
    assert all(d.is_dir() for d in keep_overlay_dirs)
    # And the dropped Drawing's run really is gone.
    assert session.get(Run, drop_run.id) is None


def test_delete_drawing_with_no_runs_or_gt_removes_it(session, tmp_path):
    cache_root = tmp_path / "drawings"
    drawing = Drawing(name="bare")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    (cache_root / str(drawing.id)).mkdir(parents=True)

    counts = DrawingService(
        session, cache_root=cache_root, overlay_root=tmp_path / "overlays"
    ).delete_drawing(drawing.id)

    assert counts.runs == 0
    assert counts.results == 0
    assert session.get(Drawing, drawing.id) is None
    assert not (cache_root / str(drawing.id)).exists()


def test_delete_drawing_tolerates_already_missing_page_dir(session, tmp_path):
    cache_root = tmp_path / "drawings"
    drawing = Drawing(name="bare")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    # No page-image dir on disk at all — the delete must not blow up.

    counts = DrawingService(
        session, cache_root=cache_root, overlay_root=tmp_path / "overlays"
    ).delete_drawing(drawing.id)

    assert counts.runs == 0
    assert session.get(Drawing, drawing.id) is None


def test_delete_drawing_unknown_id_raises(session, tmp_path):
    service = DrawingService(
        session, cache_root=tmp_path / "drawings", overlay_root=tmp_path / "overlays"
    )
    with pytest.raises(ValueError):
        service.delete_drawing(9999)
