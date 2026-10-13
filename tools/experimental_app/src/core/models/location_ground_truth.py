"""LocationGroundTruth table — the trusted per-Page labeled boxes (spec: Ground truth;
glossary: LocationGroundTruth; ADR 0002, 0003).

Location ground truth is **per Page** (ADR 0002): one row per labeled box, storing the
box in our internal normalized (0-1) ``[x_min, y_min, x_max, y_max]`` format. Boxes are
imported from an expert's native ``objects`` JSON (ADR 0003, 0022 — no in-app editor); the
importer converts each absolute pixel box using the Page's native point dimensions before
persisting. Kept DB-agnostic per ADR 0007/0008.
"""

from sqlmodel import Field, SQLModel


class LocationGroundTruth(SQLModel, table=True):
    __tablename__ = "location_ground_truth"

    id: int | None = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id", index=True)
    # An ObjectLabel taxonomy value (validated against the enum in the service layer).
    label: str
    # Normalized 0-1 box corners.
    x_min: float
    y_min: float
    x_max: float
    y_max: float
