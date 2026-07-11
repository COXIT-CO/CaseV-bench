"""Foundation smoke tests: the app boots, the DB round-trips, the adapter is stubbable."""

from pathlib import Path

from adapters.openrouter import get_openrouter_adapter
from models.meta import AppMeta


def test_base_page_renders_with_htmx(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Prompt &amp; Config Lab" in response.text
    assert "htmx.org" in response.text  # HTMX wired into the base layout


def test_schema_created_and_row_round_trips(session):
    session.add(AppMeta(key="schema_ready", value="yes"))
    session.commit()

    assert session.get(AppMeta, "schema_ready").value == "yes"


def test_adapter_is_overridable_and_returns_canned_response(app, stub_adapter):
    provider = app.dependency_overrides[get_openrouter_adapter]
    assert provider() is stub_adapter

    stub_adapter.responses["some/model"] = '{"cabinets": 3}'
    result = stub_adapter.send_image_prompt(
        Path("page.png"), "some/model", "count them"
    )

    assert result["choices"][0]["message"]["content"] == '{"cabinets": 3}'
    assert stub_adapter.calls[0]["model"] == "some/model"
