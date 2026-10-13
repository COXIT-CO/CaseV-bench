import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, MetaData, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SCHEMA = "service"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = {
        "comment": "One weekly-benchmark CI run of the raw location-prediction pipeline.",
    }

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    pages_total: Mapped[int] = mapped_column(Integer)
    pages_scored: Mapped[int] = mapped_column(Integer)
    prompt_path: Mapped[str] = mapped_column(Text)
    prompt_sha256: Mapped[str] = mapped_column(Text)
    render_px_sent: Mapped[int] = mapped_column(Integer)
    render_provider_cap: Mapped[int] = mapped_column(Integer)
    render_effective_dpi: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    generation_temperature: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    generation_max_output_tokens: Mapped[int] = mapped_column(Integer)
    dataset_dir: Mapped[str] = mapped_column(Text)
    dataset_version: Mapped[str] = mapped_column(Text)
    scorer_version: Mapped[str] = mapped_column(Text)
    canonical_iou_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    iou_sweep: Mapped[list[float]] = mapped_column(JSONB)
    cost_spent_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunDocument(Base):
    __tablename__ = "run_documents"
    __table_args__ = {
        "comment": "One scored (run, document) measurement per row.",
    }

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.runs.run_id", ondelete="CASCADE")
    )
    document_id: Mapped[str] = mapped_column(Text)
    iou_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    tp: Mapped[int] = mapped_column(Integer)
    fp: Mapped[int] = mapped_column(Integer)
    fn: Mapped[int] = mapped_column(Integer)
    precision: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    recall: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    f1: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    scorer_output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunPage(Base):
    __tablename__ = "run_pages"
    __table_args__ = {
        "comment": "One attempted page per row: raw response, cost, tokens and latency.",
    }

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.runs.run_id", ondelete="CASCADE")
    )
    document_id: Mapped[str] = mapped_column(Text)
    page: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


metadata = Base.metadata
