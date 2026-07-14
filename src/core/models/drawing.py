"""Drawing & Page tables — the uploaded PDF and its ingested pages (spec: Drawings
& ingestion; glossary: Drawing, Page).

A ``Drawing`` is one uploaded PDF. Each ``Page`` caches the reference to its
rendered + downsampled image (produced once at ingest) and stores that image's
pixel dimensions, needed later to normalize imported location boxes (ticket 10).
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
    # Pixel dimensions of the cached image (used to normalize location boxes later).
    width_px: int
    height_px: int

    drawing: Drawing | None = Relationship(back_populates="pages")
