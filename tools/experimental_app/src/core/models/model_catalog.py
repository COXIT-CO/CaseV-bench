"""Model catalog table — the curated list of vision models a Run can select
(spec: Models; glossary: Model).

A ``ModelCatalogEntry`` is one known OpenRouter model slug offered in the selection
UI as a checkbox. The slug is the natural key: a Run stores the chosen slug string
on the run/result (spec data model), so nothing references this table by id — it is
seeded reference data, not a relational parent. Free-text one-off slugs are *not*
persisted here; they live only in a run's selection. Kept DB-agnostic per ADR 0007/0008.
"""

from sqlmodel import Field, SQLModel


class ModelCatalogEntry(SQLModel, table=True):
    __tablename__ = "model_catalog_entry"

    # OpenRouter slug, e.g. ``anthropic/claude-sonnet-4.5``.
    slug: str = Field(primary_key=True)
    # Human-friendly label shown next to the checkbox.
    label: str
