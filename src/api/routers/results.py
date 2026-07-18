"""Result drill-down (spec §A.3): the scored breakdown for one Result plus the prediction
overlay PNG, all under ``/api`` for the SPA."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from api.deps import get_session
from core.models.drawing import Drawing
from core.models.prompt import Prompt, Task
from core.models.results import OBJECT_LABELS, LocationResult
from core.models.run import Prediction, Result, Run
from core.services.scoring import ScoringService

router = APIRouter(prefix="/api", tags=["results"])


class CountingLabelDetail(BaseModel):
    """One label's counting breakdown (mirrors ``scoring.LabelScore``, from
    ``Score.per_label_json``): the summed prediction, the GT total, and the derived
    absolute error + exact-match flag the drill-down table shows."""

    label: str
    predicted: int
    gt: int
    absolute_error: int
    exact_match: bool


class CountingScoreOut(BaseModel):
    """A counting Result's Score block: the two ranked aggregates plus the per-label rows."""

    total_absolute_error: int
    exact_match_count: int
    per_label: list[CountingLabelDetail]


class LocationLabelDetail(BaseModel):
    """One label's location breakdown (mirrors ``scoring.LabelLocationScore``): the
    IoU@0.5 tally and its derived rates."""

    label: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


class LocationScoreOut(BaseModel):
    """A location Result's Score block: the micro-averaged P/R/F1 headline plus the
    per-label rows (subtitle "IoU@0.5, matched per page then micro-averaged")."""

    precision: float
    recall: float
    f1: float
    per_label: list[LocationLabelDetail]


class PredictionOut(BaseModel):
    """One Page's Prediction in the drill-down: the raw model output and parsed JSON on
    success, or a parse-error failure record. ``box_count`` is the number of predicted boxes
    on the page (location only, 0 otherwise)."""

    page_number: int
    status: str
    raw_content: str | None
    parsed_json: str | None
    parse_error: str | None
    box_count: int


class ResultDetailResponse(BaseModel):
    """``GET /api/results/{id}`` (spec §A.3): the header refs, the correct score shape for
    the Run's Task with per-label detail (``null`` when the Drawing has no GT — unscored,
    distinct from scored-zero), and the per-page Predictions. Exactly one of
    ``counting_score`` / ``location_score`` is set, per the Task."""

    result_id: int
    model: str
    task: str
    prompt_family: str
    prompt_version: int
    run_id: int
    drawing_id: int
    drawing_name: str
    scored: bool
    label_count: int
    counting_score: CountingScoreOut | None = None
    location_score: LocationScoreOut | None = None
    predictions: list[PredictionOut]


def _prediction_out(pred: Prediction, task: Task) -> PredictionOut:
    """Shape one Prediction for the SPA. The predicted box count is parsed from the stored
    location boxes whether the Prediction is a scored ``ok`` or a salvaged ``error`` (ADR
    0019) — both carry ``parsed_json``; counting rows and box-less failures report 0."""
    box_count = 0
    if task == Task.location and pred.parsed_json:
        box_count = len(LocationResult.model_validate_json(pred.parsed_json).detections)
    return PredictionOut(
        page_number=pred.page_number,
        status=pred.status.value,
        raw_content=pred.raw_content,
        parsed_json=pred.parsed_json,
        parse_error=pred.parse_error,
        box_count=box_count,
    )


@router.get("/results/{result_id}", response_model=ResultDetailResponse)
def result_detail(
    result_id: int, session: Session = Depends(get_session)
) -> ResultDetailResponse:
    """JSON twin of the retired Jinja ``/results/{id}`` drill-down (spec §A.3). Scored on
    the Run's own Task so a location Result uses IoU@0.5 P/R/F1 and never gets clobbered by
    counting scoring; the Score is recomputed against current GT on read (ADR 0004), so a
    GT import after the Run shows up without a re-run. Returns ``scored:false`` with a null
    score block when the Drawing has no GT (unscored, distinct from a zero score)."""
    result = session.get(Result, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no result with id {result_id}")
    run = session.get(Run, result.run_id)
    prompt = session.get(Prompt, run.prompt_id)
    drawing = session.get(Drawing, run.drawing_id)
    scoring = ScoringService(session)

    if run.task == Task.location:
        score = scoring.score_location_result(result_id)
    else:
        score = scoring.score_result(result_id)

    counting_score: CountingScoreOut | None = None
    location_score: LocationScoreOut | None = None
    if score is not None:
        per_label = json.loads(score.per_label_json)
        if run.task == Task.location:
            location_score = LocationScoreOut(
                precision=score.precision,
                recall=score.recall,
                f1=score.f1,
                per_label=[LocationLabelDetail(**ls) for ls in per_label],
            )
        else:
            counting_score = CountingScoreOut(
                total_absolute_error=score.total_absolute_error,
                exact_match_count=score.exact_match_count,
                per_label=[CountingLabelDetail(**ls) for ls in per_label],
            )

    return ResultDetailResponse(
        result_id=result.id,
        model=result.model,
        task=run.task.value,
        prompt_family=prompt.family,
        prompt_version=prompt.version,
        run_id=run.id,
        drawing_id=drawing.id,
        drawing_name=drawing.name,
        scored=score is not None,
        label_count=len(OBJECT_LABELS),
        counting_score=counting_score,
        location_score=location_score,
        predictions=[_prediction_out(pred, run.task) for pred in result.predictions],
    )


# The prediction overlay PNG, served under /api for the SPA (spec §A.3) — the same stored
# PNG the retired Jinja run-status fragment once used. The /api prefix keeps it off the
# client-side routes so the SPA's own /results/:id path still falls through to index.html
# in dev.
@router.get("/results/{result_id}/pages/{page_number}/overlay")
def result_overlay(
    result_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> Response:
    """The location prediction-overlay PNG for one (Result, Page): the model's boxes drawn
    on the page image during the Run, served from the cached file (ticket 09)."""
    prediction = session.exec(
        select(Prediction).where(
            Prediction.result_id == result_id,
            Prediction.page_number == page_number,
        )
    ).first()
    if (
        prediction is None
        or not prediction.overlay_path
        or not Path(prediction.overlay_path).exists()
    ):
        raise HTTPException(status_code=404, detail="Overlay not found")
    return FileResponse(prediction.overlay_path, media_type="image/png")
