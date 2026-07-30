"""PDF -> per-page PNG preprocessing, backed by PyMuPDF (fitz)."""
from __future__ import annotations

import uuid

import fitz  # PyMuPDF

from app.core.config import Settings
from app.models.schemas import PageInfo
from app.services.image_utils import downscale_image
from app.services.session_store import PageRecord, SessionData, session_store

PDF_MAGIC = b"%PDF-"

# Bounds for the free-numeric DPI input (frontend enforces the same range;
# this is the backend's guard against absurd values from a direct API call).
DPI_MIN = 1
DPI_MAX = 2400


class InvalidPDFError(Exception):
    pass


class FileTooLargeError(Exception):
    pass


class InvalidDPIError(Exception):
    pass


def validate_pdf_bytes(data: bytes, settings: Settings) -> None:
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise FileTooLargeError(
            f"File exceeds the {settings.max_file_size_mb}MB upload limit."
        )
    if not data.startswith(PDF_MAGIC):
        raise InvalidPDFError("File does not look like a valid PDF.")


def process_pdf_upload(
    filename: str, data: bytes, settings: Settings, dpi: int | None = None
) -> tuple[SessionData, list[PageInfo]]:
    """Validate, persist, and render a freshly uploaded PDF into per-page PNGs.

    `dpi` is the per-upload override from the UI's DPI control; falls back to
    the config-level default (`settings.pdf_dpi`) when not given (e.g. a
    direct API call that doesn't set it).

    Returns the new session record plus the page metadata the frontend needs
    to render a thumbnail grid.
    """
    validate_pdf_bytes(data, settings)

    if dpi is not None and not (DPI_MIN <= dpi <= DPI_MAX):
        raise InvalidDPIError(f"dpi must be between {DPI_MIN} and {DPI_MAX}.")

    effective_dpi = dpi if dpi else settings.pdf_dpi

    session_id = uuid.uuid4().hex[:12]
    session_dir = settings.data_dir / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = session_dir / "source.pdf"
    pdf_path.write_bytes(data)

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # pymupdf raises assorted errors on malformed PDFs
        raise InvalidPDFError(f"Could not open PDF: {exc}") from exc

    if doc.page_count == 0:
        doc.close()
        raise InvalidPDFError("PDF has no pages.")

    zoom = effective_dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    pages: dict[str, PageRecord] = {}
    page_infos: list[PageInfo] = []

    try:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            pix = page.get_pixmap(matrix=matrix)
            page_number = index + 1
            page_id = f"page-{page_number}"
            image_path = session_dir / f"{page_id}.png"
            pix.save(image_path)

            # Precomputed once here (not on every image request) since a
            # large-format sheet at high DPI can be big enough that the
            # browser's own image decoder rejects the full-resolution
            # original outright -- see image_utils.py.
            display_bytes, _ = downscale_image(pix.tobytes("png"), "image/png")
            display_image_path = session_dir / f"{page_id}-display.png"
            display_image_path.write_bytes(display_bytes)

            pages[page_id] = PageRecord(
                page_id=page_id,
                page_number=page_number,
                image_path=image_path,
                display_image_path=display_image_path,
                width=pix.width,
                height=pix.height,
            )
            page_infos.append(
                PageInfo(
                    page_id=page_id,
                    page_number=page_number,
                    width=pix.width,
                    height=pix.height,
                    image_url=f"/api/sessions/{session_id}/pages/{page_id}/image",
                )
            )
    finally:
        doc.close()

    session_data = SessionData(
        session_id=session_id,
        filename=filename,
        session_dir=session_dir,
        pages=pages,
        dpi=effective_dpi,
    )
    session_store.create(session_data)

    return session_data, page_infos
