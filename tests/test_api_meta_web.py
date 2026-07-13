"""Slice-0 proof endpoint (ticket 01): the JSON API is mounted under ``/api`` beside the
live HTMX app, so the SPA shell can verify the Vite -> JSON -> shadcn pipeline end to end.
"""

from models.prompt import Task
from models.results import OBJECT_LABELS


def test_api_meta_returns_app_facts(client):
    response = client.get("/api/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "Prompt & Config Lab"
    assert body["tasks"] == [t.value for t in Task]
    assert body["labels"] == list(OBJECT_LABELS)
    # A fresh test DB has no domain rows yet; the counts come straight off the DB.
    assert body["drawing_count"] == 0
    assert body["run_count"] == 0
    assert body["result_count"] == 0


def test_api_layer_is_additive_htmx_index_stays_live(client):
    # Coexistence (ADR 0010): the JSON layer is purely additive; the Jinja ``/`` route is
    # left untouched and still serves the live HTMX app during the migration.
    response = client.get("/")

    assert response.status_code == 200
    assert "htmx.org" in response.text
