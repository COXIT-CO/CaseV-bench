"""The ticket-08 cutover (ADR-0010 contract step): the React SPA owns ``/`` and the
Jinja/HTMX layer is gone. The whole web surface is now the JSON API + binary-asset routes
under ``/api`` plus the built SPA.

These drive the serving logic against a *fixture* build (``built_spa``) rather than a real
``vite build`` output, so the Python suite never depends on the frontend having been built.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.app
from adapters.openrouter import get_openrouter_adapter
from web.app import create_app

# The client-side routes react-router owns (App.tsx). A refresh / deep link on any of these
# must reach the server, miss every /api route, and fall back to the SPA shell.
CLIENT_ROUTES = [
    "/",
    "/runs",
    "/runs/1",
    "/prompts",
    "/prompts/counting/default",
    "/library/drawings",
    "/library/drawings/1",
    "/library/models",
    "/results/1",
]


@pytest.fixture
def built_spa(tmp_path) -> Path:
    """A minimal stand-in for ``vite build`` output: an index.html shell + one hashed asset."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><title>Prompt &amp; Config Lab</title>'
        '<script type="module" src="/assets/index-abc123.js"></script></head>'
        '<body><div id="root"></div></body></html>'
    )
    (dist / "assets" / "index-abc123.js").write_text("console.log('spa');")
    return dist


@pytest.fixture
def spa_client(engine, stub_adapter, built_spa):
    app = create_app(engine=engine, spa_dist=built_spa)
    app.dependency_overrides[get_openrouter_adapter] = lambda: stub_adapter
    with TestClient(app) as client:
        yield client


def test_root_serves_the_spa_shell(spa_client):
    response = spa_client.get("/")

    assert response.status_code == 200
    assert '<div id="root">' in response.text
    # The retired HTMX layer is gone: no template markup ever comes back.
    assert "htmx" not in response.text


@pytest.mark.parametrize("path", CLIENT_ROUTES)
def test_every_client_route_falls_back_to_the_shell(spa_client, path):
    # The smoke check the ticket asks for: every primary area loads on a hard refresh.
    response = spa_client.get(path)

    assert response.status_code == 200
    assert '<div id="root">' in response.text


def test_hashed_assets_are_served(spa_client):
    response = spa_client.get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_api_still_serves_json(spa_client):
    response = spa_client.get("/api/meta")

    assert response.status_code == 200
    assert response.json()["app"] == "Prompt & Config Lab"


def test_unknown_api_path_404s_as_json_not_the_shell(spa_client):
    # An unmatched /api/* path must not silently fall through to the SPA shell.
    response = spa_client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert "<div id=" not in response.text


def test_missing_build_is_a_helpful_hint_not_a_crash(engine, tmp_path):
    # Build output isn't committed, so a fresh checkout has no dist. The app must still boot
    # and serve the JSON API; only the SPA route degrades to a build hint.
    app = create_app(engine=engine, spa_dist=tmp_path / "never-built")
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 503
        assert "build" in root.text.lower()

        assert client.get("/api/meta").status_code == 200


def test_no_jinja_template_dir_remains():
    # No dead Jinja/HTMX template is left in the tree after the cutover.
    templates = Path(web.app.__file__).parent / "templates"
    assert not templates.exists()
