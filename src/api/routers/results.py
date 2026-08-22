"""Result drill-down (spec §A.3): the scored breakdown for one Result plus the prediction
overlay PNG, all under ``/api`` for the SPA."""

import json
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from api.deps import get_session
from api.routers.common import KnobsOut
from core.models.drawing import Drawing
from core.models.prompt import Prompt
from core.models.results import OBJECT_LABELS, LocationResult
from core.models.run import Prediction, Result, Run
from core.services.prediction_override import (
    PredictionOverrideError,
    PredictionOverrideService,
)
from core.services.scoring import LOCATION_IOU_THRESHOLD, ScoringService

router = APIRouter(prefix="/api", tags=["results"])


class LocationLabelDetail(BaseModel):
    """One label's location breakdown (mirrors ``scoring.LabelLocationScore``): the
    matched tally and its derived rates, at the response's echoed ``iou_threshold``."""

    label: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


class LocationScoreOut(BaseModel):
    """A location Result's Score block: the micro-averaged P/R/F1 headline plus the
    per-label rows. The operating point is the response's ``iou_threshold``, not a fixed
    0.5 — the subtitle renders that echoed value."""

    precision: float
    recall: float
    f1: float
    per_label: list[LocationLabelDetail]


class PredictionOut(BaseModel):
    """One Page's Prediction in the drill-down: the raw model output and parsed JSON on
    success, or a parse-error failure record. ``box_count`` is the number of predicted boxes
    on the page — the **edited** count when an override is set, so
    the card's box tally matches the redrawn overlay. ``edited_json`` is the developer's manual
    override (ADR 0020, ticket 07): ``null`` unless the box JSON was edited; scoring ignores it
    entirely, so an edited result never out-ranks an unedited one."""

    page_number: int
    status: str
    raw_content: str | None
    parsed_json: str | None
    parse_error: str | None
    box_count: int
    edited_json: str | None


class ResultDetailResponse(BaseModel):
    """``GET /api/results/{id}`` (spec §A.3): the header refs, the Score with per-label
    detail (``null`` when the Drawing has no GT — unscored, distinct from scored-zero), and
    the per-page Predictions."""

    result_id: int
    model: str
    prompt_family: str
    prompt_version: int
    run_id: int
    drawing_id: int
    drawing_name: str
    scored: bool
    label_count: int
    knobs: KnobsOut
    # The operating point these rates were computed at, and whether it is CaseV's canonical
    # one. Echoed rather than assumed so the drill-down can label an exploratory board
    # honestly instead of printing a hardcoded "IoU@0.5" over 0.3 numbers.
    iou_threshold: float
    canonical_iou: bool
    canonical_iou_threshold: float
    location_score: LocationScoreOut | None = None
    predictions: list[PredictionOut]


def _prediction_out(pred: Prediction) -> PredictionOut:
    """Shape one Prediction for the SPA. The predicted box count is parsed from the stored
    boxes whether the Prediction is a scored ``ok`` or a salvaged ``error`` (ADR 0019) — both
    carry ``parsed_json``; a box-less failure reports 0. When a manual override is set (ADR
    0020) the count comes from the **edited** boxes, so the box tally matches the redrawn
    overlay while the Score still uses the untouched original.
    """
    box_count = 0
    boxes = pred.edited_json or pred.parsed_json
    if boxes:
        box_count = len(LocationResult.model_validate_json(boxes).detections)
    return PredictionOut(
        page_number=pred.page_number,
        status=pred.status.value,
        raw_content=pred.raw_content,
        parsed_json=pred.parsed_json,
        parse_error=pred.parse_error,
        box_count=box_count,
        edited_json=pred.edited_json,
    )


@router.get("/results/{result_id}", response_model=ResultDetailResponse)
def result_detail(
    result_id: int,
    iou_threshold: float | None = Query(default=None, gt=0.0, le=1.0),
    session: Session = Depends(get_session),
) -> ResultDetailResponse:
    """JSON twin of the retired Jinja ``/results/{id}`` drill-down (spec §A.3). The Score is
    P/R/F1 at the canonical operating point, recomputed against current GT on read (ADR
    0004), so a GT import after the Run shows up without a re-run. Returns ``scored:false``
    with a null score block when the Drawing has no GT (unscored, distinct from a zero
    score).

    ``iou_threshold`` re-scores this Result at an arbitrary operating point for exploration
    (scope: IoU knob). It persists nothing — the service routes it to
    ``explore_location_result``, which has no write path — so inspecting which boxes flip
    TP/FP at 0.3 leaves the canonical Score row untouched. The threshold in force is always
    echoed back, so the drill-down never has to assume which one produced its numbers.
    """
    result = session.get(Result, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no result with id {result_id}")
    run = session.get(Run, result.run_id)
    prompt = session.get(Prompt, run.prompt_id)
    drawing = session.get(Drawing, run.drawing_id)

    scoring = ScoringService(session)
    effective_iou = (
        iou_threshold if iou_threshold is not None else LOCATION_IOU_THRESHOLD
    )
    location_score: LocationScoreOut | None = None
    if iou_threshold is None:
        score = scoring.score_location_result(result_id)
        if score is not None:
            location_score = LocationScoreOut(
                precision=score.precision,
                recall=score.recall,
                f1=score.f1,
                per_label=[
                    LocationLabelDetail(**ls) for ls in json.loads(score.per_label_json)
                ],
            )
    else:
        score = scoring.explore_location_result(result_id, iou_threshold)
        if score is not None:
            location_score = LocationScoreOut(
                precision=score.precision,
                recall=score.recall,
                f1=score.f1,
                per_label=[LocationLabelDetail(**asdict(ls)) for ls in score.per_label],
            )

    return ResultDetailResponse(
        result_id=result.id,
        model=result.model,
        prompt_family=prompt.family,
        prompt_version=prompt.version,
        run_id=run.id,
        drawing_id=drawing.id,
        drawing_name=drawing.name,
        scored=score is not None,
        label_count=len(OBJECT_LABELS),
        iou_threshold=effective_iou,
        canonical_iou=iou_threshold is None,
        canonical_iou_threshold=LOCATION_IOU_THRESHOLD,
        knobs=KnobsOut(
            dpi=run.dpi,
            downsample_px=run.downsample_px,
            max_tokens=run.max_tokens,
            temperature=run.temperature,
        ),
        location_score=location_score,
        predictions=[_prediction_out(pred) for pred in result.predictions],
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
    if prediction is None:
        raise HTTPException(status_code=404, detail="Overlay not found")
    # An edited Prediction (ADR 0020, ticket 07) redraws on demand from ``edited_json`` — the
    # boxes the developer corrected — reusing the shared renderer, caching nothing. Only an
    # unedited Prediction falls back to the cached run-time overlay PNG.
    if prediction.edited_json:
        png = PredictionOverrideService(session).edited_overlay_png(
            result_id, page_number
        )
        return Response(content=png, media_type="image/png")
    if not prediction.overlay_path or not Path(prediction.overlay_path).exists():
        raise HTTPException(status_code=404, detail="Overlay not found")
    return FileResponse(prediction.overlay_path, media_type="image/png")


class PredictionOverrideRequest(BaseModel):
    """``PUT /api/results/{id}/pages/{n}/prediction`` body (ADR 0020, ticket 07): the developer's
    corrected box JSON as a string (a ``LocationResult`` — ``{"detections": [...]}``). Carried as
    a string, not a typed model, so *invalid* JSON is rejected by the override service with a
    precise ``400`` (and nothing persisted) rather than a generic ``422`` from body parsing.
    """

    edited_json: str


@router.put(
    "/results/{result_id}/pages/{page_number}/prediction",
    response_model=PredictionOut,
)
def set_prediction_override(
    result_id: int,
    page_number: int,
    payload: PredictionOverrideRequest,
    session: Session = Depends(get_session),
) -> PredictionOut:
    """Set a location Prediction's manual JSON override (ADR 0020, ticket 07): validate the
    corrected boxes against ``LocationResult`` (taxonomy labels, 0–1 coords) and persist them,
    leaving the model's original output and its Score untouched. Invalid JSON / a bad label /
    an out-of-range coordinate is a ``400`` with a precise message and nothing persisted; an
    unknown (Result, page) is a ``404``. Returns the updated Prediction so the SPA redraws
    the overlay and shows the badge.
    """
    service = PredictionOverrideService(session)
    try:
        prediction = service.set_override(result_id, page_number, payload.edited_json)
    except LookupError:
        raise HTTPException(status_code=404, detail="Prediction not found")
    except PredictionOverrideError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _prediction_out(prediction)


@router.delete(
    "/results/{result_id}/pages/{page_number}/prediction",
    response_model=PredictionOut,
)
def revert_prediction_override(
    result_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> PredictionOut:
    """Revert a location Prediction to the model's output by clearing its override (ADR 0020,
    ticket 07); a no-op when unedited. An unknown (Result, page) is a ``404``. Returns the
    reverted Prediction."""
    service = PredictionOverrideService(session)
    try:
        prediction = service.revert(result_id, page_number)
    except LookupError:
        raise HTTPException(status_code=404, detail="Prediction not found")
    except PredictionOverrideError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _prediction_out(prediction)
