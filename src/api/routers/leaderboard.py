"""``GET /api/leaderboard`` — the ranked board of prompt-version × model Configurations
(spec §A.2). The service ranks once and returns rows already ordered; this router only
serializes and enriches each row with its Drawing."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.deps import get_session
from api.routers.common import LeaderboardDrawing
from core.models.drawing import Drawing
from core.models.prompt import Task
from core.models.results import OBJECT_LABELS
from core.models.run import Run
from core.services.scoring import LocationLeaderboardMetric, ScoringService

router = APIRouter(prefix="/api", tags=["leaderboard"])


class LeaderboardRowOut(BaseModel):
    """One Leaderboard line — a prompt-version × model Configuration outcome and its IoU@0.5
    rates. Unscored rows (no GT) have ``rank:null`` and null metrics — a claim about the row,
    not about a ``Score`` — and the service has already pinned them last.
    ``drawing_id``/``drawing_name`` let the SPA show the Drawing column and point an unscored
    row's CTA at its ground-truth entry (§B.2).
    """

    rank: int | None
    result_id: int
    run_id: int
    model: str
    prompt_family: str
    prompt_version: int
    drawing_id: int
    drawing_name: str
    scored: bool
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None


class LeaderboardResponse(BaseModel):
    """The board plus the filter surface the SPA mirrors into the URL (spec §A.2): the
    resolved Drawing/sort, the valid ``sort`` metrics, the Drawing dropdown, the taxonomy
    size, and the already-ranked rows.

    ``task`` is now a constant echo — there is one board (ADR 0032) — kept only so the SPA's
    still-Task-shaped contract keeps parsing until ticket 04 flattens it. ``label_count`` is
    the fixed taxonomy's size; it was the exact-match denominator the counting board ranked
    on, and it outlives that use as the row count a per-label breakdown covers."""

    task: str
    drawing_id: int | None
    prompt_family: str | None
    prompt_version: int | None
    sort: str
    metrics: list[str]
    drawings: list[LeaderboardDrawing]
    label_count: int
    rows: list[LeaderboardRowOut]


def _parse_metric(sort: str | None) -> LocationLeaderboardMetric:
    """Resolve a ``?sort=`` value to a ranking metric, falling back to F1 for a
    missing/unknown one so a stale URL never 500s (mirrors the Jinja route's
    ``_parse_metric``)."""
    try:
        return LocationLeaderboardMetric(sort)
    except ValueError:
        return LocationLeaderboardMetric.f1


@router.get("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(
    drawing_id: int | None = None,
    prompt_family: str | None = None,
    prompt_version: int | None = None,
    sort: str | None = None,
    session: Session = Depends(get_session),
) -> LeaderboardResponse:
    """JSON twin of the Jinja ``/leaderboard`` (spec §A.2). Same service method, so the
    board is ranked once in the service (``_rank_location``) and returned already ordered —
    the SPA renders, it does not re-rank. An unknown ``sort`` falls back to F1. ``rank`` is
    the 1-based index over scored rows so the SPA needn't re-derive it.

    Optional ``prompt_family`` / ``prompt_version`` narrow the board to one prompt lineage
    or pin one exact version (ticket 04). A ``prompt_version`` without a ``prompt_family``
    is a ``400`` — versions are per-family, so a bare version is ambiguous."""
    if prompt_version is not None and prompt_family is None:
        raise HTTPException(
            status_code=400,
            detail="prompt_version requires prompt_family (versions are per-family)",
        )

    metric = _parse_metric(sort)
    rows = ScoringService(session).location_leaderboard(
        drawing_id,
        metric=metric,
        prompt_family=prompt_family,
        prompt_version=prompt_version,
    )

    # The board rows carry only run_id; look the Drawing up once per Run so a row can show
    # its Drawing and build the GT CTA — enrichment in the web layer, no service change.
    run_meta = {
        run_id: (d_id, d_name)
        for run_id, d_id, d_name in session.exec(
            select(Run.id, Drawing.id, Drawing.name).join(
                Drawing, Run.drawing_id == Drawing.id
            )
        ).all()
    }

    drawings = session.exec(select(Drawing).order_by(Drawing.created_at.desc())).all()

    out_rows: list[LeaderboardRowOut] = []
    rank = 0
    for row in rows:
        if row.scored:
            rank += 1
        d_id, d_name = run_meta[row.run_id]
        out_rows.append(
            LeaderboardRowOut(
                rank=rank if row.scored else None,
                result_id=row.result_id,
                run_id=row.run_id,
                model=row.model,
                prompt_family=row.prompt_family,
                prompt_version=row.prompt_version,
                drawing_id=d_id,
                drawing_name=d_name,
                scored=row.scored,
                precision=row.precision,
                recall=row.recall,
                f1=row.f1,
            )
        )

    return LeaderboardResponse(
        task=Task.location.value,
        drawing_id=drawing_id,
        prompt_family=prompt_family,
        prompt_version=prompt_version,
        sort=metric.value,
        metrics=[m.value for m in LocationLeaderboardMetric],
        drawings=[
            LeaderboardDrawing(id=d.id, name=d.name, page_count=len(d.pages))
            for d in drawings
        ],
        label_count=len(OBJECT_LABELS),
        rows=out_rows,
    )
