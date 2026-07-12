"""Model catalog & selection (ticket 04).

Two seams:
- ``ModelCatalogService.resolve_selection`` — combine checked seeded slugs with
  free-text slugs into the plain list a Run consumes.
- ``seed_defaults`` / ``list_catalog`` — the curated seed the checkboxes render.
Plus a web check that the selection component round-trips through ``/models``.
"""

from services.model_catalog import DEFAULT_MODEL_CATALOG, ModelCatalogService

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


def test_models_page_renders_seeded_checkboxes_and_free_text(client):
    response = client.get("/models")
    assert response.status_code == 200
    for slug in (SONNET, GPT, GEMINI):
        assert slug in response.text
    assert 'type="checkbox"' in response.text
    assert 'name="free_text"' in response.text


def test_post_selection_returns_slug_list_including_free_text(client):
    response = client.post(
        "/models",
        data={"models": [SONNET, GPT], "free_text": "mistralai/pixtral-12b"},
    )
    assert response.status_code == 200
    assert SONNET in response.text
    assert GPT in response.text
    assert "mistralai/pixtral-12b" in response.text
