"""Ground-truth entry (ticket 07), hung off the Drawing detail. Recording GT for a Drawing
turns its previously-unscored Leaderboard/Result rows into scored ones with **no re-run**
(scores recompute on read). Two entry points: a counting number-per-label form and a native
``objects`` location import — both reusing the existing services unchanged; only the web layer
differs.
"""

import json

from fastapi import APIRouter, Body, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from api.deps import get_session
from core.models.drawing import Drawing
from core.models.results import OBJECT_LABELS
from core.services.counting_ground_truth import CountingGroundTruthService
from core.services.location_ground_truth import LocationGroundTruthService

router = APIRouter(prefix="/api", tags=["ground-truth"])


class CountingGtLabel(BaseModel):
    """One taxonomy label's stored total, or ``null`` when it has not been entered yet — the
    null lets the form tell "unentered" from "entered zero" (matches the service's
    ``get_totals`` semantics)."""

    name: str
    value: int | None


class CountingGroundTruthResponse(BaseModel):
    """``GET``/``PUT /api/drawings/{id}/counting-ground-truth``: the per-label totals in the
    fixed taxonomy order. The same shape is returned for pre-fill and after a save (the
    just-saved totals), so the SPA can seed the form and update its cache from one shape.
    """

    drawing_id: int
    labels: list[CountingGtLabel]


class ImportProblemOut(BaseModel):
    """One object the native import reported rather than silently dropped: an off-taxonomy
    category (``unmapped_label``), a reference to a page the Drawing lacks (``unknown_page``),
    or a box that grossly overflows its page's native frame (``out_of_frame``)."""

    kind: str
    detail: str


class LocationImportResponse(BaseModel):
    """``POST /api/drawings/{id}/location-ground-truth``: the ``LocationImportResult`` — how
    many boxes were created and every problem reported (spec §A.6)."""

    created: int
    problems: list[ImportProblemOut]


def _counting_gt_response(
    drawing_id: int, totals: dict[str, int]
) -> CountingGroundTruthResponse:
    """Build the pre-fill/saved response from a label→total map, filling every taxonomy
    label in order and leaving an unentered label ``null``."""
    return CountingGroundTruthResponse(
        drawing_id=drawing_id,
        labels=[
            CountingGtLabel(name=label, value=totals.get(label))
            for label in OBJECT_LABELS
        ],
    )


@router.get(
    "/drawings/{drawing_id}/counting-ground-truth",
    response_model=CountingGroundTruthResponse,
)
def counting_ground_truth(
    drawing_id: int, session: Session = Depends(get_session)
) -> CountingGroundTruthResponse:
    """Pre-fill the counting form with each taxonomy label's stored total (spec §A.6). An
    unentered label is ``null`` so the form distinguishes it from an entered zero. Unknown
    Drawing → ``404``."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    totals = CountingGroundTruthService(session).get_totals(drawing_id)
    return _counting_gt_response(drawing_id, totals)


@router.put(
    "/drawings/{drawing_id}/counting-ground-truth",
    response_model=CountingGroundTruthResponse,
)
def save_counting_ground_truth(
    drawing_id: int,
    payload: dict = Body(...),
    session: Session = Depends(get_session),
) -> CountingGroundTruthResponse:
    """Upsert the per-label counting totals (spec §A.6). Every taxonomy label is required —
    a missing or non-integer total is a ``400`` (the same rule the retired Jinja form
    enforced), validated in-route to keep the ``{"detail": …}`` envelope (a Pydantic model
    would raise ``422``). Returns the saved totals. Unknown Drawing → ``404``."""
    drawing = session.get(Drawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    try:
        totals = {label: payload[label] for label in OBJECT_LABELS}
    except KeyError:
        raise HTTPException(
            status_code=400, detail="A total is required for every label"
        )
    # Require a real integer per label — reject strings and floats (a truncated 2.7 would
    # be a silent wrong answer) rather than coercing. ``bool`` is an ``int`` subclass, so
    # exclude it explicitly.
    if any(isinstance(v, bool) or not isinstance(v, int) for v in totals.values()):
        raise HTTPException(status_code=400, detail="Every total must be an integer")
    CountingGroundTruthService(session).save(drawing_id, totals)
    return _counting_gt_response(drawing_id, totals)


@router.post(
    "/drawings/{drawing_id}/location-ground-truth",
    response_model=LocationImportResponse,
)
async def import_location_ground_truth(
    drawing_id: int,
    file: UploadFile,
    derive_counting: bool = Form(False),
    session: Session = Depends(get_session),
) -> LocationImportResponse:
    """Import a native ``objects`` JSON upload as this Drawing's LocationGroundTruth via the
    importer (spec §A.6), surfacing its problem report (off-taxonomy categories / unknown
    pages / out-of-frame boxes) instead of silently dropping them. The file needs no
    configuration — the label map is the identity and each box states its page. A Drawing with
    no source PDF (image-ingested, so no native page frame to normalize against), a non-JSON
    file, or a non-object body is a ``400``; an unknown Drawing is a ``404``. Re-importing
    replaces the Drawing's existing boxes.

    ``derive_counting`` (a default-off form field — ADR 0025) additionally writes the counting
    GT from the accepted boxes; left unset, the import touches only location GT and any
    existing counting total is preserved."""
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

    result = LocationGroundTruthService(session).import_objects(
        drawing_id, document, derive_counting=derive_counting
    )

    return LocationImportResponse(
        created=result.created,
        problems=[
            ImportProblemOut(kind=problem.kind, detail=problem.detail)
            for problem in result.problems
        ],
    )
