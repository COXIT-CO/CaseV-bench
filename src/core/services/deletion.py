"""Shared cascade-delete machinery (ADR-0016).

Deletes are manual service-layer code — the schema has no DB-level ``ON DELETE`` (ADR-0016)
— so a delete must remove child rows and on-disk artifacts together and tolerate an
already-missing file. The one primitive every entity delete bottoms out in is *remove these
Runs and everything under them*: each Run's Results, their Predictions and Scores, and the
location prediction-overlay PNGs cached per Result (``overlay_root/<result_id>/``). The Run
delete (ticket 07) passes a single Run; the Drawing (ticket 08) and Prompt (ticket 09)
deletes gather the Runs they cascade to and hand them here, so the cascade + file cleanup
lives in exactly one place.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from core.models.run import Prediction, Result, Run
from core.models.score import Score


@dataclass(frozen=True)
class RunCascadeCounts:
    """The collateral a Run cascade removed, for the confirmation dialog and the delete
    response (ADR-0016): how many Runs and how many Results were deleted."""

    runs: int
    results: int


def cascade_delete_runs(
    session: Session, run_ids: list[int], overlay_root: Path
) -> RunCascadeCounts:
    """Delete the given Runs and everything under them, committing once, then remove each
    Result's cached overlay directory.

    Returns the collateral counts (Runs + Results removed). Runs already gone are simply
    not counted, so a caller that resolved ids from a stale read still gets an accurate
    tally. Overlay cleanup tolerates an already-missing directory (the PNGs are a
    regenerable cache), so a vanished file never blocks the delete.
    """
    runs = session.exec(select(Run).where(Run.id.in_(run_ids))).all()
    results = session.exec(select(Result).where(Result.run_id.in_(run_ids))).all()
    result_ids = [r.id for r in results]
    scores = session.exec(select(Score).where(Score.result_id.in_(result_ids))).all()
    predictions = session.exec(
        select(Prediction).where(Prediction.result_id.in_(result_ids))
    ).all()

    # Child-first so the delete is FK-safe regardless of whether the backend enforces
    # foreign keys: Scores + Predictions, then Results, then the Runs themselves.
    for row in (*scores, *predictions, *results, *runs):
        session.delete(row)
    session.commit()

    for result_id in result_ids:
        _remove_overlay_dir(overlay_root / str(result_id))

    return RunCascadeCounts(runs=len(runs), results=len(results))


def _remove_overlay_dir(path: Path) -> None:
    """Remove a Result's overlay directory, tolerating one that is already gone."""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
