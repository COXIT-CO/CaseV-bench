"""Model catalog & selection — seed the curated model list and turn a form
submission into the plain slug list a Run consumes (ticket 04).

The curated slugs are seeded into SQLite (ADR 0007: SQLite is the sole store; known
code-time data is seeded, like the prompt ``.md`` files). ``resolve_selection`` is a
pure function over form input — checked seeded slugs plus a free-text escape hatch —
so it needs no database and is trivially testable.
"""

import re

from sqlmodel import Session, select

from core.models.model_catalog import ModelCatalogEntry

# The curated seed: known vision-capable OpenRouter slugs (the POC's three).
DEFAULT_MODEL_CATALOG: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"),
    ("openai/gpt-5-mini", "GPT-5 mini"),
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
]

# Free-text may be pasted comma-, newline-, or whitespace-separated; slugs have no
# internal whitespace, so any run of these is a safe delimiter.
_FREE_TEXT_DELIMITERS = re.compile(r"[\s,]+")


class ModelCatalogService:
    def __init__(self, session: Session):
        self.session = session

    def seed_defaults(self) -> None:
        """Insert any curated slugs not already present. Idempotent."""
        for slug, label in DEFAULT_MODEL_CATALOG:
            if self.session.get(ModelCatalogEntry, slug) is None:
                self.session.add(ModelCatalogEntry(slug=slug, label=label))
        self.session.commit()

    def list_catalog(self) -> list[ModelCatalogEntry]:
        """The curated entries to render as checkboxes, in a stable order."""
        return list(
            self.session.exec(
                select(ModelCatalogEntry).order_by(ModelCatalogEntry.slug)
            ).all()
        )

    def add(self, slug: str, label: str) -> ModelCatalogEntry:
        """Add a catalog entry, or re-label it if the slug already exists — an upsert on the
        natural key (ticket 10). Adding a known slug therefore never errors or duplicates a
        row; it just updates the label. Both fields are trimmed and required (empty → error).
        No OpenRouter validation: a bad slug simply fails per-model at run time (ticket 10).
        """
        slug = slug.strip()
        label = label.strip()
        if not slug:
            raise ValueError("model slug is required")
        if not label:
            raise ValueError("model label is required")
        entry = self.session.get(ModelCatalogEntry, slug)
        if entry is None:
            entry = ModelCatalogEntry(slug=slug, label=label)
            self.session.add(entry)
        else:
            entry.label = label
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def remove(self, slug: str) -> ModelCatalogEntry:
        """Remove a catalog entry by slug, returning it as the delete's receipt. Removing an
        entry never touches past Runs — a Run stores the chosen slug string, not a reference
        to this table (ADR 0016, ticket 10). An unknown slug raises ``ValueError``."""
        entry = self.session.get(ModelCatalogEntry, slug)
        if entry is None:
            raise ValueError(f"no model catalog entry with slug {slug!r}")
        # A detached copy so the caller can still read it after the row is gone.
        removed = ModelCatalogEntry(slug=entry.slug, label=entry.label)
        self.session.delete(entry)
        self.session.commit()
        return removed

    @staticmethod
    def resolve_selection(
        selected_slugs: list[str], free_text: str | None = None
    ) -> list[str]:
        """Combine checked seeded slugs with parsed free-text slugs into the plain,
        de-duplicated, order-preserving list of slugs a Run runs against."""
        free_text_slugs = (
            _FREE_TEXT_DELIMITERS.split(free_text.strip()) if free_text else []
        )
        resolved: list[str] = []
        for slug in [*selected_slugs, *free_text_slugs]:
            slug = slug.strip()
            if slug and slug not in resolved:
                resolved.append(slug)
        return resolved
