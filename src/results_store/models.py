import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, MetaData, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Every table here belongs to `experiments`, the shared research schema. The service's own
# tables live in `service`, are owned by a different role, and are migrated by a different
# Alembic project — see `alembic/env.py`, which refuses to look outside this schema.
SCHEMA = "experiments"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class RunResult(Base):
    __tablename__ = "run_results"
    __table_args__ = {
        "comment": "One scored (model, document) measurement per row. The shared research store.",
    }

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model: Mapped[str] = mapped_column(Text)
    document_id: Mapped[str] = mapped_column(Text)
    # Free text, author-namespaced: `author/sliding_window`.
    config_label: Mapped[str] = mapped_column(Text)
    iou_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    scorer_version: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tp: Mapped[int] = mapped_column(Integer)
    fp: Mapped[int] = mapped_column(Integer)
    fn: Mapped[int] = mapped_column(Integer)
    precision: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    recall: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    f1: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    # `location-scorer`'s return value, whole. It carries `per_type`, `per_page` and `best_iou`.
    #  A summary cannot be un-summarized later, so the blob is stored rather than reduced.
    scorer_output: Mapped[dict[str, Any]] = mapped_column(JSONB)


# Alembic's env.py targets `metadata`. Exposing `Base.metadata` under the old name keeps that
# contract — and the schema binding — unchanged after the move to declarative mapping.
metadata = Base.metadata
