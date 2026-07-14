"""Delete-a-Run service seam (ticket 07, ADR-0016).

Deleting a Run must remove the Run and everything under it — its Results, their
Predictions and Scores, and the location prediction-overlay PNGs cached per Result — in
one cascade, return the collateral counts the confirmation showed, leave every unrelated
Run untouched, and tolerate an already-missing overlay file. A real location Run is
launched through the shared service (adapter stubbed) so the rows and on-disk overlays
exist exactly as production writes them, then deleted.
"""

import json
from pathlib import Path

from PIL import Image
from sqlmodel import select

from core.models.drawing import Drawing, Page
from core.models.prompt import Task
from core.models.run import Prediction, Result, Run
from core.models.score import Score
from core.services.prompt import PromptService
from core.services.run import RunService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"

BOXES_JSON = json.dumps(
    [
        {
            "label": "cabinets",
            "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4},
        }
    ]
)


def _seed_drawing(session, tmp_path: Path, name: str, n_pages: int = 2) -> Drawing:
    """A Drawing whose Pages point at real PNGs so overlay rendering has an image."""
    drawing = Drawing(name=name)
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    for page_number in range(1, n_pages + 1):
        image_path = tmp_path / f"{name}_page_{page_number}.png"
        Image.new("RGB", (100, 100), "white").save(image_path)
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=str(image_path),
                width_px=100,
                height_px=100,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def _launch_location_run(session, stub_adapter, tmp_path, overlay_root, name: str):
    """Launch a 2-model, 2-page location Run so it has Results, Predictions, and overlays."""
    drawing = _seed_drawing(session, tmp_path, name)
    prompt = PromptService(session).create(Task.location, name, "find them")
    stub_adapter.responses = {SONNET: BOXES_JSON, GPT: BOXES_JSON}
    return RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [SONNET, GPT]
    )


def test_delete_run_removes_rows_files_and_returns_counts(
    session, stub_adapter, tmp_path
):
    overlay_root = tmp_path / "overlays"
    run = _launch_location_run(session, stub_adapter, tmp_path, overlay_root, "target")

    result_ids = [r.id for r in run.results]
    prediction_ids = session.exec(
        select(Prediction.id).where(Prediction.result_id.in_(result_ids))
    ).all()
    overlay_dirs = [overlay_root / str(rid) for rid in result_ids]
    # A Score attaches to a Result and must go with it.
    session.add(Score(result_id=result_ids[0], per_label_json="[]"))
    session.commit()

    # Preconditions: rows and overlay files are all present.
    assert len(result_ids) == 2
    assert len(prediction_ids) == 4
    assert all(d.is_dir() and any(d.iterdir()) for d in overlay_dirs)

    counts = RunService(session, stub_adapter, overlay_root=overlay_root).delete_run(
        run.id
    )

    assert counts.runs == 1
    assert counts.results == 2

    # Every row is gone.
    assert session.get(Run, run.id) is None
    assert session.exec(select(Result).where(Result.id.in_(result_ids))).all() == []
    assert (
        session.exec(select(Prediction).where(Prediction.id.in_(prediction_ids))).all()
        == []
    )
    assert (
        session.exec(select(Score).where(Score.result_id.in_(result_ids))).all() == []
    )
    # The overlay files are cleaned up.
    assert not any(d.exists() for d in overlay_dirs)


def test_delete_run_leaves_other_runs_untouched(session, stub_adapter, tmp_path):
    overlay_root = tmp_path / "overlays"
    keep = _launch_location_run(session, stub_adapter, tmp_path, overlay_root, "keep")
    drop = _launch_location_run(session, stub_adapter, tmp_path, overlay_root, "drop")

    keep_result_ids = [r.id for r in keep.results]
    keep_overlay_dirs = [overlay_root / str(rid) for rid in keep_result_ids]

    RunService(session, stub_adapter, overlay_root=overlay_root).delete_run(drop.id)

    # The untouched Run keeps its rows and its overlay files.
    assert session.get(Run, keep.id) is not None
    surviving = session.exec(select(Result).where(Result.run_id == keep.id)).all()
    assert {r.id for r in surviving} == set(keep_result_ids)
    assert session.exec(
        select(Prediction).where(Prediction.result_id.in_(keep_result_ids))
    ).all()
    assert all(d.is_dir() for d in keep_overlay_dirs)


def test_delete_run_tolerates_already_missing_overlay_file(
    session, stub_adapter, tmp_path
):
    import shutil

    overlay_root = tmp_path / "overlays"
    run = _launch_location_run(session, stub_adapter, tmp_path, overlay_root, "target")
    result_ids = [r.id for r in run.results]

    # Simulate an overlay directory that vanished out from under us.
    shutil.rmtree(overlay_root / str(result_ids[0]))

    counts = RunService(session, stub_adapter, overlay_root=overlay_root).delete_run(
        run.id
    )

    assert counts.results == 2
    assert session.get(Run, run.id) is None
    assert not (overlay_root / str(result_ids[1])).exists()


def test_delete_run_unknown_id_raises(session, stub_adapter, tmp_path):
    import pytest

    service = RunService(session, stub_adapter, overlay_root=tmp_path / "overlays")
    with pytest.raises(ValueError):
        service.delete_run(9999)
