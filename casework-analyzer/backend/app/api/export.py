"""Download endpoints for benchmark-compatible export formats
(see dataset/project-*/prj*-obj-{count,location}.json in the main repo)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.services.parser import build_count_export, build_location_export
from app.services.session_store import session_store

router = APIRouter(tags=["export"])


def _project_id_from_filename(filename: str) -> str:
    return Path(filename).stem


@router.get("/sessions/{session_id}/export/count")
def export_count(session_id: str) -> JSONResponse:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")

    payload = build_count_export(list(session.results.values()))
    return JSONResponse(
        content=payload.model_dump(),
        headers={"Content-Disposition": "attachment; filename=obj-count.json"},
    )


@router.get("/sessions/{session_id}/export/locations")
def export_locations(session_id: str) -> JSONResponse:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")

    project_id = _project_id_from_filename(session.filename)
    page_dimensions = {
        page.page_number: (page.width, page.height)
        for page in session.pages.values()
    }
    payload = build_location_export(
        project_id, list(session.results.values()), page_dimensions
    )
    return JSONResponse(
        content=payload.model_dump(),
        headers={"Content-Disposition": "attachment; filename=obj-location.json"},
    )
