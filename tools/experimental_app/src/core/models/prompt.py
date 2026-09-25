"""Prompt table — immutable, versioned instruction text (spec: Prompts; glossary:
Prompt; ADR 0009).

A Prompt row is one ``(family, version, text)``. "Editing" a prompt never mutates a row —
it inserts the next version in the same ``family`` lineage, so a Run can pin exact text
forever. Kept DB-agnostic per ADR 0007/0008.
"""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel, UniqueConstraint


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Prompt(SQLModel, table=True):
    __tablename__ = "prompt"
    # One row per (family, version): the tuple identity can never be reused, which is what
    # keeps a version pinned by a Run immutable (ADR 0009).
    __table_args__ = (UniqueConstraint("family", "version", name="uq_prompt_version"),)

    id: int | None = Field(default=None, primary_key=True)
    family: str = Field(index=True)
    version: int
    text: str
    created_at: datetime = Field(default_factory=_utcnow)
