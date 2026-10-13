"""create service tables

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("pages_total", sa.Integer(), nullable=False),
        sa.Column("pages_scored", sa.Integer(), nullable=False),
        sa.Column("prompt_path", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.Text(), nullable=False),
        sa.Column("render_px_sent", sa.Integer(), nullable=False),
        sa.Column("render_provider_cap", sa.Integer(), nullable=False),
        sa.Column(
            "render_effective_dpi", sa.Numeric(precision=8, scale=4), nullable=False
        ),
        sa.Column(
            "generation_temperature", sa.Numeric(precision=4, scale=3), nullable=False
        ),
        sa.Column("generation_max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("dataset_dir", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("scorer_version", sa.Text(), nullable=False),
        sa.Column(
            "canonical_iou_threshold", sa.Numeric(precision=4, scale=3), nullable=False
        ),
        sa.Column("iou_sweep", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cost_spent_usd", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("run_id"),
        schema="service",
        comment="One weekly-benchmark CI run of the raw location-prediction pipeline.",
    )
    op.create_table(
        "run_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("iou_threshold", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("tp", sa.Integer(), nullable=False),
        sa.Column("fp", sa.Integer(), nullable=False),
        sa.Column("fn", sa.Integer(), nullable=False),
        sa.Column("precision", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("recall", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("f1", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column(
            "scorer_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["service.runs.run_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="service",
        comment="One scored (run, document) measurement per row.",
    )
    op.create_table(
        "run_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("finish_reason", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("latency_seconds", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["service.runs.run_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="service",
        comment="One attempted page per row: raw response, cost, tokens and latency.",
    )


def downgrade() -> None:
    op.drop_table("run_pages", schema="service")
    op.drop_table("run_documents", schema="service")
    op.drop_table("runs", schema="service")
