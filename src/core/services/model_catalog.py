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
