"""Drawing & Page tables — the uploaded PDF and its ingested pages (spec: Drawings
& ingestion; glossary: Drawing, Page).

A ``Drawing`` is one uploaded PDF or plain image (ticket 11). Each ``Page`` caches
the reference to its rendered + downsampled image (produced once at ingest) and
stores two frames: the *full-resolution* raster's pixel dimensions — not the
downsample's — used for prediction overlays/display, and (for a PDF page) the
*native point dimensions* of the source PDF page (``page.rect``), the 72-DPI frame
human-expert location ground truth is annotated against and normalized by (ADR 0022).
Kept DB-agnostic per ADR 0007/0008.
"""

from datetime import datetime, timezone

from sqlmodel import Field, Relationship, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Drawing(SQLModel, table=True):
    __tablename__ = "drawing"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    # Path to the retained source upload (a PDF), so a Run can re-render its Pages on demand
    # at the chosen DPI (ADR 0018). ``None`` for an image-ingested Drawing — it has no PDF, so
    # DPI is ignored and its Pages render from the stored native raster instead.
    source_path: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    pages: list["Page"] = Relationship(
        back_populates="drawing",
        sa_relationship_kwargs={"order_by": "Page.page_number"},
    )


class Page(SQLModel, table=True):
    __tablename__ = "page"

    id: int | None = Field(default=None, primary_key=True)
    drawing_id: int = Field(foreign_key="drawing.id", index=True)
    # 1-based page number within the Drawing.
    page_number: int
    # Path to the cached rendered + downsampled page image on disk.
    image_path: str
    # Full-resolution (pre-downsample) pixel dimensions of the rendered raster — the basis
    # for prediction overlays/display, not the downsample's dims.
    width_px: int
    height_px: int
    # Native point dimensions of the source PDF page (``page.rect``): the 72-DPI raster where
    # 1 px = 1 PDF point. This is the frame location ground truth is normalized by (ADR 0022),
    # captured at ingest so the native GT importer needs no PDF I/O. ``None`` for an
    # image-ingested Page (no source PDF) — the importer reads this as "no native frame
    # available" and rejects the import.
    native_width_pt: float | None = None
    native_height_pt: float | None = None

    drawing: Drawing | None = Relationship(back_populates="pages")
