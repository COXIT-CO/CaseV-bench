from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

# Every table here belongs to `experiments`, the shared research schema. The service's own
# tables live in `service`, are owned by a different role, and are migrated by a different
# Alembic project — see `alembic/env.py`, which refuses to look outside this schema.
SCHEMA = "experiments"

metadata = MetaData(schema=SCHEMA)

run_results = Table(
    "run_results",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("model", Text, nullable=False),
    Column("document_id", Text, nullable=False),
    # Free text, author-namespaced: `author/sliding_window`.
    Column("config_label", Text, nullable=False),
    Column("iou_threshold", Numeric(4, 3), nullable=False),
    Column("scorer_version", Text, nullable=False),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("tp", Integer, nullable=False),
    Column("fp", Integer, nullable=False),
    Column("fn", Integer, nullable=False),
    Column("precision", Numeric(6, 5), nullable=False),
    Column("recall", Numeric(6, 5), nullable=False),
    Column("f1", Numeric(6, 5), nullable=False),
    # `location-scorer`'s return value, whole. It carries `per_type`, `per_page` and `best_iou`.
    #  A summary cannot be un-summarized later, so the blob is stored rather than reduced.
    Column("scorer_output", JSONB, nullable=False),
    comment="One scored (model, document) measurement per row. The shared research store.",
)
