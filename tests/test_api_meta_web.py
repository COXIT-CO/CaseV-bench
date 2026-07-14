"""Slice-0 proof endpoint (ticket 01): the SPA shell fetches ``GET /api/meta`` to verify
the Vite -> JSON -> shadcn pipeline end to end. Since the ticket-08 cutover the JSON API is
the data half of the web surface; the SPA serving half is covered in test_spa_serving.py.
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
