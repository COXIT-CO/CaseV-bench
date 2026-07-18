"""Delete-a-Prompt service seam (ticket 09, ADR-0016).

A Prompt is deletable at two granularities, both cascading to the Runs that pinned the
affected version(s) — reusing the shared Run-deletion machinery (their Results,
Predictions, Scores, and overlay files go too):

- a single **version** — only the Runs pinning that exact version are cascaded; the
  family's other versions and their Runs survive, even when the deleted version sits
  mid-lineage (a cosmetic v1, v3 gap);
- a whole **family** — every version and every Run pinning any of them is removed.

Unrelated families and versions are always left untouched. Real location Runs are launched
through the shared service (adapter stubbed) so the rows and on-disk overlays exist exactly
as production writes them, then the prompt is deleted.
"""

import json
from pathlib import Path

import pytest
from conftest import seed_page_images
from sqlmodel import select

from core.models.drawing import Drawing, Page
from core.models.prompt import Prompt, Task
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


def _seed_drawing(session, tmp_path: Path, name: str, n_pages: int = 1) -> Drawing:
    """A Drawing whose Pages point at real native rasters (one dir per drawing) so
    render-on-demand and overlay rendering both have an image."""
    drawing = Drawing(name=name)
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    images = seed_page_images(tmp_path / str(drawing.id), n_pages)
    for page_number, image in enumerate(images, start=1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=str(image),
                width_px=100,
                height_px=100,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def _launch(session, stub_adapter, overlay_root, prompt_id: int, drawing) -> Run:
    """Launch a 2-model location Run pinning ``prompt_id`` so it has Results, Predictions,
    and rendered overlays."""
    stub_adapter.responses = {SONNET: BOXES_JSON, GPT: BOXES_JSON}
    return RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        Task.location, prompt_id, drawing.id, [SONNET, GPT]
    )


def _versioned_family(session, family: str, n: int) -> list[Prompt]:
    """Author ``family`` v1..vN and return the version rows, oldest-first."""
    service = PromptService(session)
    service.create(Task.location, family, "v1 text")
    for _ in range(2, n + 1):
        service.edit(Task.location, family, "next text")
    return service.history(Task.location, family)[::-1]


def _overlay_dirs(overlay_root: Path, run: Run) -> list[Path]:
    return [overlay_root / str(r.id) for r in run.results]


def test_delete_version_cascades_only_its_runs_and_returns_counts(
    session, stub_adapter, tmp_path
):
    overlay_root = tmp_path / "overlays"
    drawing = _seed_drawing(session, tmp_path, "d")
    v1, v2 = _versioned_family(session, "cabinets", 2)
    run_v1 = _launch(session, stub_adapter, overlay_root, v1.id, drawing)
    run_v2 = _launch(session, stub_adapter, overlay_root, v2.id, drawing)

    v1_result_ids = [r.id for r in run_v1.results]
    v1_prediction_ids = session.exec(
        select(Prediction.id).where(Prediction.result_id.in_(v1_result_ids))
    ).all()
    v1_overlays = _overlay_dirs(overlay_root, run_v1)
    v2_overlays = _overlay_dirs(overlay_root, run_v2)
    session.add(Score(result_id=v1_result_ids[0], per_label_json="[]"))
    session.commit()
    assert all(d.is_dir() for d in (*v1_overlays, *v2_overlays))

    counts = PromptService(session, overlay_root=overlay_root).delete_version(
        Task.location, "cabinets", v1.version
    )

    # The collateral is exactly the Runs + Results that pinned v1.
    assert counts.runs == 1
    assert counts.results == 2

    # v1 and everything that pinned it is gone.
    assert session.get(Prompt, v1.id) is None
    assert session.get(Run, run_v1.id) is None
    assert session.exec(select(Result).where(Result.id.in_(v1_result_ids))).all() == []
    assert (
        session.exec(
            select(Prediction).where(Prediction.id.in_(v1_prediction_ids))
        ).all()
        == []
    )
    assert (
        session.exec(select(Score).where(Score.result_id.in_(v1_result_ids))).all()
        == []
    )
    assert not any(d.exists() for d in v1_overlays)

    # v2 and its Run survive untouched.
    assert session.get(Prompt, v2.id) is not None
    assert session.get(Run, run_v2.id) is not None
    assert all(d.is_dir() for d in v2_overlays)


def test_delete_mid_lineage_version_leaves_a_harmless_gap(
    session, stub_adapter, tmp_path
):
    overlay_root = tmp_path / "overlays"
    drawing = _seed_drawing(session, tmp_path, "d")
    v1, v2, v3 = _versioned_family(session, "doors", 3)
    run_v2 = _launch(session, stub_adapter, overlay_root, v2.id, drawing)

    counts = PromptService(session, overlay_root=overlay_root).delete_version(
        Task.location, "doors", v2.version
    )

    assert counts.runs == 1
    # The surrounding versions are intact — history reads v3, v1 with a cosmetic v2 gap.
    remaining = [
        p.version for p in PromptService(session).history(Task.location, "doors")
    ]
    assert remaining == [3, 1]
    assert session.get(Prompt, v1.id) is not None
    assert session.get(Prompt, v3.id) is not None
    assert session.get(Run, run_v2.id) is None


def test_delete_family_cascades_every_version_and_its_runs(
    session, stub_adapter, tmp_path
):
    overlay_root = tmp_path / "overlays"
    drawing = _seed_drawing(session, tmp_path, "d")
    v1, v2 = _versioned_family(session, "windows", 2)
    run_v1 = _launch(session, stub_adapter, overlay_root, v1.id, drawing)
    run_v2 = _launch(session, stub_adapter, overlay_root, v2.id, drawing)
    all_overlays = _overlay_dirs(overlay_root, run_v1) + _overlay_dirs(
        overlay_root, run_v2
    )
    assert all(d.is_dir() for d in all_overlays)

    counts = PromptService(session, overlay_root=overlay_root).delete_family(
        Task.location, "windows"
    )

    # Both versions' Runs (2 runs × 2 models) are the collateral.
    assert counts.runs == 2
    assert counts.results == 4

    # The whole family and every Run pinning any version is gone.
    assert PromptService(session).history(Task.location, "windows") == []
    assert session.get(Prompt, v1.id) is None
    assert session.get(Prompt, v2.id) is None
    assert session.get(Run, run_v1.id) is None
    assert session.get(Run, run_v2.id) is None
    assert not any(d.exists() for d in all_overlays)


def test_delete_family_leaves_unrelated_families_untouched(
    session, stub_adapter, tmp_path
):
    overlay_root = tmp_path / "overlays"
    drawing = _seed_drawing(session, tmp_path, "d")
    (keep_v1,) = _versioned_family(session, "keep", 1)
    (drop_v1,) = _versioned_family(session, "drop", 1)
    keep_run = _launch(session, stub_adapter, overlay_root, keep_v1.id, drawing)
    drop_run = _launch(session, stub_adapter, overlay_root, drop_v1.id, drawing)
    keep_overlays = _overlay_dirs(overlay_root, keep_run)

    PromptService(session, overlay_root=overlay_root).delete_family(
        Task.location, "drop"
    )

    # The other family, its version, and its Run all survive.
    assert session.get(Prompt, keep_v1.id) is not None
    assert session.get(Run, keep_run.id) is not None
    assert all(d.is_dir() for d in keep_overlays)
    # And the dropped family's run really is gone.
    assert session.get(Run, drop_run.id) is None


def test_collateral_counts_match_the_delete_for_both_granularities(
    session, stub_adapter, tmp_path
):
    overlay_root = tmp_path / "overlays"
    drawing = _seed_drawing(session, tmp_path, "d")
    v1, v2 = _versioned_family(session, "fixtures", 2)
    _launch(session, stub_adapter, overlay_root, v1.id, drawing)
    _launch(session, stub_adapter, overlay_root, v2.id, drawing)
    _launch(session, stub_adapter, overlay_root, v2.id, drawing)

    service = PromptService(session, overlay_root=overlay_root)
    by_version = service.collateral_by_version(Task.location, "fixtures")

    # One Run pinned v1 (2 results); two Runs pinned v2 (4 results).
    assert by_version[v1.version].runs == 1
    assert by_version[v1.version].results == 2
    assert by_version[v2.version].runs == 2
    assert by_version[v2.version].results == 4


def test_delete_unknown_version_and_family_raise(session, tmp_path):
    service = PromptService(session, overlay_root=tmp_path / "overlays")
    _versioned_family(session, "present", 1)
    with pytest.raises(ValueError):
        service.delete_version(Task.location, "present", 99)
    with pytest.raises(ValueError):
        service.delete_family(Task.location, "ghost")
