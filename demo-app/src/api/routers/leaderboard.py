"""``GET /api/leaderboard`` — the ranked board of prompt-version × model Configurations
(spec §A.2). The service ranks once and returns rows already ordered; this router only
serializes and enriches each row with its Drawing."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from api.deps import get_session
from api.routers.common import LeaderboardDrawing
from core.models.drawing import Drawing
from core.models.results import OBJECT_LABELS
from core.models.run import Run
from core.services.scoring import (
    LOCATION_IOU_THRESHOLD,
    LocationLeaderboardMetric,
    ScoringService,
)

router = APIRouter(prefix="/api", tags=["leaderboard"])


class LeaderboardRowOut(BaseModel):
    """One Leaderboard line — a prompt-version × model Configuration outcome and its rates
    at the response's echoed ``iou_threshold``. Unscored rows (no GT) have ``rank:null`` and
    null metrics — a claim about the row, not about a ``Score`` — and the service has already
    pinned them last.
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

    ``label_count`` is the fixed taxonomy's size — the row count a per-label breakdown
    covers."""

    drawing_id: int | None
    prompt_family: str | None
    prompt_version: int | None
    sort: str
    metrics: list[str]
    drawings: list[LeaderboardDrawing]
    label_count: int
    # The operating point the board was ranked at, plus whether that is CaseV's canonical
    # one. The SPA needs both: the value to label the rates with, the flag to decide whether
    # to show the "exploring" badge and suppress anything that treats the board as official.
    iou_threshold: float
    canonical_iou: bool
    canonical_iou_threshold: float
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
    iou_threshold: float | None = Query(default=None, gt=0.0, le=1.0),
    session: Session = Depends(get_session),
) -> LeaderboardResponse:
    """JSON twin of the Jinja ``/leaderboard`` (spec §A.2). Same service method, so the
    board is ranked once in the service (``_rank_location``) and returned already ordered —
    the SPA renders, it does not re-rank. An unknown ``sort`` falls back to F1. ``rank`` is
    the 1-based index over scored rows so the SPA needn't re-derive it.

    Optional ``prompt_family`` / ``prompt_version`` narrow the board to one prompt lineage
    or pin one exact version (ticket 04). A ``prompt_version`` without a ``prompt_family``
    is a ``400`` — versions are per-family, so a bare version is ambiguous.

    ``iou_threshold`` re-ranks the board at an arbitrary operating point for exploration.
    Omitted, the board is the canonical one and Scores are recomputed and persisted as
    always; supplied, the service computes in memory and **writes nothing**, so a browse at
    0.3 cannot move the published numbers. The threshold in force and the canonical one are
    both echoed, so no client has to hardcode either."""
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
        iou_threshold=iou_threshold,
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
        iou_threshold=(
            iou_threshold if iou_threshold is not None else LOCATION_IOU_THRESHOLD
        ),
        canonical_iou=iou_threshold is None,
        canonical_iou_threshold=LOCATION_IOU_THRESHOLD,
        rows=out_rows,
    )
