"""Benchmark scoring against an uploaded ground-truth file (see
services/scoring.py, wrapping packages/location-scorer)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import ScoreRequest
from app.services.scoring import score_session
from app.services.session_store import session_store

router = APIRouter(tags=["scoring"])


@router.post("/sessions/{session_id}/score")
def score(session_id: str, request: ScoreRequest) -> dict:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")

    page_dimensions = {
        page.page_number: (page.width, page.height) for page in session.pages.values()
    }
    return score_session(
        results=list(session.results.values()),
        page_dimensions=page_dimensions,
        ground_truth=request.ground_truth,
        iou_threshold=request.iou_threshold,
        dpi=session.dpi,
        ground_truth_space=request.ground_truth_space,
        include_objects=request.include_objects,
    )
