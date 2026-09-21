"""Ground-truth entry (ticket 07), hung off the Drawing detail. Recording GT for a Drawing
turns its previously-unscored Leaderboard/Result rows into scored ones with **no re-run**
(scores recompute on read). One entry point: a native ``objects`` location import, reusing
the existing service unchanged; only the web layer differs.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from api.deps import get_session
from core.models.drawing import Drawing
from core.services.location_ground_truth import LocationGroundTruthService

router = APIRouter(prefix="/api", tags=["ground-truth"])


class ImportProblemOut(BaseModel):
    """One object the native import reported rather than silently dropped: an off-taxonomy
    category (``unmapped_label``), a reference to a page the Drawing lacks (``unknown_page``),
    a box that grossly overflows its page's native frame (``out_of_frame``), or one enclosing
    no area — zero width/height or inverted coordinates (``degenerate_box``)."""

    kind: str
    detail: str


class LocationImportResponse(BaseModel):
    """``POST /api/drawings/{id}/location-ground-truth``: the ``LocationImportResult`` — how
    many boxes were created and every problem reported (spec §A.6)."""

    created: int
    problems: list[ImportProblemOut]


@router.post(
    "/drawings/{drawing_id}/location-ground-truth",
    response_model=LocationImportResponse,
)
async def import_location_ground_truth(
    drawing_id: int,
    file: UploadFile,
    session: Session = Depends(get_session),
) -> LocationImportResponse:
    """Import a native ``objects`` JSON upload as this Drawing's LocationGroundTruth via the
    importer (spec §A.6), surfacing its problem report (off-taxonomy categories / unknown
    pages / out-of-frame boxes) instead of silently dropping them. The file needs no
    configuration — the label map is the identity and each box states its page. A Drawing with
    no source PDF (image-ingested, so no native page frame to normalize against), a non-JSON
    file, or a non-object body is a ``400``; an unknown Drawing is a ``404``. Re-importing
    replaces the Drawing's existing boxes."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    if drawing.source_path is None:
        # No source PDF → no native point frame; normalizing against nothing would be wrong.
        raise HTTPException(
            status_code=400,
            detail="Drawing has no source PDF; location ground truth needs native page "
            "dimensions from a PDF import",
        )

    try:
        document = json.loads(await file.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Uploaded file is not valid JSON")
    if not isinstance(document, dict):
        raise HTTPException(
            status_code=400, detail="Location ground truth JSON must be an object"
        )

    result = LocationGroundTruthService(session).import_objects(drawing_id, document)

    return LocationImportResponse(
        created=result.created,
        problems=[
            ImportProblemOut(kind=problem.kind, detail=problem.detail)
            for problem in result.problems
        ],
    )
