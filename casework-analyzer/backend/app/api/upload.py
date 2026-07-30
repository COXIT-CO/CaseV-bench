"""PDF upload + preprocessing endpoint, and page image serving."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.config import Settings, get_settings
from app.models.schemas import UploadResponse
from app.services.pdf_processor import (
    FileTooLargeError,
    InvalidDPIError,
    InvalidPDFError,
    process_pdf_upload,
)
from app.services.session_store import session_store

router = APIRouter(tags=["upload"])


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    dpi: int | None = Form(None),
    settings: Settings = Depends(get_settings),
) -> UploadResponse:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted.")

    data = await file.read()

    try:
        session, pages = process_pdf_upload(
            file.filename or "upload.pdf", data, settings, dpi=dpi
        )
    except FileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except InvalidPDFError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidDPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return UploadResponse(
        session_id=session.session_id,
        filename=session.filename,
        page_count=len(pages),
        pages=pages,
    )


@router.get("/sessions/{session_id}/pages/{page_id}/image")
def get_page_image(session_id: str, page_id: str) -> FileResponse:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")
    page = session.pages.get(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Unknown page.")
    # The downscaled copy, not the full-resolution original -- see
    # services/image_utils.py for why the browser needs this.
    return FileResponse(page.display_image_path, media_type="image/png")
