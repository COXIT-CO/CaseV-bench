"""Model catalog & selection (ticket 04).

Two seams:
- ``ModelCatalogService.resolve_selection`` — combine checked seeded slugs with
  free-text slugs into the plain list a Run consumes.
- ``seed_defaults`` / ``list_catalog`` — the curated seed the checkboxes render.

The HTMX ``/models`` selection page was retired in ticket 06 (the catalog now lives in the
React SPA against ``GET /api/models``, tested in ``test_library_api.py``); slug resolution
stays a pure-service unit test here, and ``POST /api/runs`` remains its authoritative caller.
"""

import pytest

from core.services.model_catalog import DEFAULT_MODEL_CATALOG, ModelCatalogService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"
GEMINI = "google/gemini-2.5-flash"


def test_resolve_selection_includes_free_text_slug():
    slugs = ModelCatalogService.resolve_selection(
        selected_slugs=[SONNET, GPT],
        free_text="mistralai/pixtral-12b",
    )
    assert slugs == [SONNET, GPT, "mistralai/pixtral-12b"]


def test_resolve_selection_dedupes_and_ignores_blanks_and_separators():
    # Free text may be pasted with commas/newlines/whitespace; a duplicate of a
    # checked slug collapses to one, order preserved.
    slugs = ModelCatalogService.resolve_selection(
        selected_slugs=[GPT, GPT],
        free_text=f"  {GPT} , {GEMINI} \n",
    )
    assert slugs == [GPT, GEMINI]


def test_resolve_selection_empty_yields_empty_list():
    assert ModelCatalogService.resolve_selection([], "") == []


def test_seed_defaults_is_idempotent(session):
    service = ModelCatalogService(session)
    service.seed_defaults()
    service.seed_defaults()

    slugs = [entry.slug for entry in service.list_catalog()]
    assert set(slugs) == {slug for slug, _ in DEFAULT_MODEL_CATALOG}
    assert len(slugs) == len(set(slugs))  # no duplicate rows


# --- CRUD (ticket 10): user-editable catalog ------------------------------------------

PIXTRAL = "mistralai/pixtral-12b"


def test_add_appends_a_new_entry(session):
    service = ModelCatalogService(session)
    service.seed_defaults()

    entry = service.add(PIXTRAL, "Pixtral 12B")

    assert (entry.slug, entry.label) == (PIXTRAL, "Pixtral 12B")
    # The new entry now renders as a checkbox alongside the seeded ones.
    catalog = {e.slug: e.label for e in service.list_catalog()}
    assert catalog[PIXTRAL] == "Pixtral 12B"


def test_add_existing_slug_upserts_the_label(session):
    service = ModelCatalogService(session)
    service.seed_defaults()

    # Re-adding a known slug re-labels it rather than erroring or duplicating (ticket 10).
    service.add(SONNET, "Claude (renamed)")

    catalog = service.list_catalog()
    assert [e.slug for e in catalog].count(SONNET) == 1  # no duplicate row
    labels = {e.slug: e.label for e in catalog}
    assert labels[SONNET] == "Claude (renamed)"


def test_add_trims_and_rejects_blank_slug_or_label(session):
    service = ModelCatalogService(session)

    entry = service.add(f"  {PIXTRAL}  ", "  Pixtral 12B  ")
    assert (entry.slug, entry.label) == (PIXTRAL, "Pixtral 12B")

    with pytest.raises(ValueError):
        service.add("   ", "A label")
    with pytest.raises(ValueError):
        service.add(PIXTRAL, "   ")


def test_remove_deletes_the_entry(session):
    service = ModelCatalogService(session)
    service.seed_defaults()

    removed = service.remove(GEMINI)

    assert removed.slug == GEMINI
    assert GEMINI not in {e.slug for e in service.list_catalog()}


def test_remove_unknown_slug_raises(session):
    service = ModelCatalogService(session)
    service.seed_defaults()

    with pytest.raises(ValueError):
        service.remove("nope/not-a-model")
