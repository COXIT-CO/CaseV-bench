"""CountingGroundTruth table — the trusted per-Drawing counting answer
(spec: Ground truth; glossary: CountingGroundTruth; ADR 0002).

Counting ground truth is **per Drawing** (ADR 0002): the correct total for each
object-taxonomy label across the whole PDF. Stored one row per ``(drawing, label)``
so each label's total is edited independently and read back per label; a unique
constraint keeps the pair single so an edit updates in place rather than appending.
Kept DB-agnostic per ADR 0007/0008.
"""

from sqlmodel import Field, SQLModel, UniqueConstraint


class CountingGroundTruth(SQLModel, table=True):
    __tablename__ = "counting_ground_truth"
    __table_args__ = (
        UniqueConstraint("drawing_id", "label", name="uq_counting_gt_drawing_label"),
    )

    id: int | None = Field(default=None, primary_key=True)
    drawing_id: int = Field(foreign_key="drawing.id", index=True)
    # An ObjectLabel taxonomy value (validated against the enum in the service layer).
    label: str
    # The correct total for this label across the Drawing.
    total: int
