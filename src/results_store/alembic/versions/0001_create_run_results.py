"""create run_results

Revision ID: 0001
Revises:
Create Date: 2026-08-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("config_label", sa.Text(), nullable=False),
        sa.Column("iou_threshold", sa.Numeric(4, 3), nullable=False),
        sa.Column("scorer_version", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tp", sa.Integer(), nullable=False),
        sa.Column("fp", sa.Integer(), nullable=False),
        sa.Column("fn", sa.Integer(), nullable=False),
        sa.Column("precision", sa.Numeric(6, 5), nullable=False),
        sa.Column("recall", sa.Numeric(6, 5), nullable=False),
        sa.Column("f1", sa.Numeric(6, 5), nullable=False),
        sa.Column("scorer_output", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="experiments",
        comment="One scored (model, document) measurement per row. The shared research store.",
    )


def downgrade() -> None:
    op.drop_table("run_results", schema="experiments")
