"""``GET /api/leaderboard`` — the ranked board of prompt-version × model Configurations
(spec §A.2). The service ranks once and returns rows already ordered; this router only
serializes and enriches each row with its Drawing."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from api.deps import get_session
from api.routers.common import LeaderboardDrawing
from core.models.drawing import Drawing
from core.models.prompt import Task
from core.models.results import OBJECT_LABELS
from core.models.run import Run
from core.services.scoring import (
    LeaderboardMetric,
    LocationLeaderboardMetric,
    ScoringService,
)

router = APIRouter(prefix="/api", tags=["leaderboard"])


class LeaderboardRowOut(BaseModel):
    """One Leaderboard line — a prompt-version × model Configuration outcome. Carries the
    counting pair **or** the location rates depending on the board's Task; the unused set
    stays ``null``. Unscored rows (no GT) have ``rank:null`` and null metrics, and the
    service has already pinned them last. ``drawing_id``/``drawing_name`` let the SPA show
    the Drawing column and point an unscored row's CTA at its ground-truth entry (§B.2).
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
    # Counting metrics.
    total_absolute_error: int | None = None
    exact_match_count: int | None = None
    # Location metrics.
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None


class LeaderboardResponse(BaseModel):
    """The board plus the filter surface the SPA mirrors into the URL (spec §A.2): the
    resolved Task/Drawing/sort, the Task's valid ``sort`` metrics, the Drawing dropdown,
    the taxonomy size (the exact-match denominator), and the already-ranked rows."""

    task: str
    drawing_id: int | None
    sort: str
    metrics: list[str]
    drawings: list[LeaderboardDrawing]
    label_count: int
    rows: list[LeaderboardRowOut]


def _parse_metric(sort: str | None, metric_enum, default):
    """Resolve a ``?sort=`` value to a member of ``metric_enum``, falling back to
    ``default`` for a missing/unknown metric so a stale URL never 500s (mirrors the Jinja
    route's ``_parse_metric``)."""
    try:
        return metric_enum(sort)
    except ValueError:
        return default


@router.get("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(
    task: str = Task.counting.value,
    drawing_id: int | None = None,
    sort: str | None = None,
    session: Session = Depends(get_session),
) -> LeaderboardResponse:
    """JSON twin of the Jinja ``/leaderboard`` (spec §A.2). Same service methods, so the
    board is ranked once in the service (``_rank`` / ``_rank_location``) and returned
    already ordered — the SPA renders, it does not re-rank. The Task selects both the
    ranking metrics and the row's score shape; an unknown ``sort`` falls back to the
    Task default. ``rank`` is the 1-based index over scored rows so the SPA needn't
    re-derive it."""
    scoring = ScoringService(session)
    board_task = Task.location if task == Task.location.value else Task.counting

    if board_task == Task.location:
        metric_enum, default = LocationLeaderboardMetric, LocationLeaderboardMetric.f1
        metric = _parse_metric(sort, metric_enum, default)
        rows = scoring.location_leaderboard(drawing_id, metric=metric)
    else:
        metric_enum, default = LeaderboardMetric, LeaderboardMetric.total_absolute_error
        metric = _parse_metric(sort, metric_enum, default)
        rows = scoring.leaderboard(drawing_id, metric=metric)

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
        common = dict(
            rank=rank if row.scored else None,
            result_id=row.result_id,
            run_id=row.run_id,
            model=row.model,
            prompt_family=row.prompt_family,
            prompt_version=row.prompt_version,
            drawing_id=d_id,
            drawing_name=d_name,
            scored=row.scored,
        )
        # Each board yields its own row dataclass with disjoint metric fields; set only the
        # Task's own pair/triple so the other stays null (the SPA reads by Task).
        if board_task == Task.location:
            out_rows.append(
                LeaderboardRowOut(
                    **common,
                    precision=row.precision,
                    recall=row.recall,
                    f1=row.f1,
                )
            )
        else:
            out_rows.append(
                LeaderboardRowOut(
                    **common,
                    total_absolute_error=row.total_absolute_error,
                    exact_match_count=row.exact_match_count,
                )
            )

    return LeaderboardResponse(
        task=board_task.value,
        drawing_id=drawing_id,
        sort=metric.value,
        metrics=[m.value for m in metric_enum],
        drawings=[
            LeaderboardDrawing(id=d.id, name=d.name, page_count=len(d.pages))
            for d in drawings
        ],
        label_count=len(OBJECT_LABELS),
        rows=out_rows,
    )
