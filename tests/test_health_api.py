"""``GET /api/health`` — the platform liveness probe (ticket 03, ADR-0013).

Distinct from ``/api/meta``: it must answer *without* touching the database, so Railway's
health check stays green even while the DB is briefly locked by a background run (ADR-0006)
or momentarily unavailable. These tests pin both properties — the shape and the no-DB
contract.
"""

from fastapi.testclient import TestClient

from api.app import create_app
from core.adapters.openrouter import get_openrouter_adapter


def test_health_returns_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_database(stub_adapter):
    # Build the app against a bogus engine that raises the moment anyone opens a session:
    # the health probe must still answer, proving it never issues a query.
    class ExplodingEngine:
        def connect(self, *args, **kwargs):
            raise AssertionError("health must not touch the database")

        def raw_connection(self, *args, **kwargs):
            raise AssertionError("health must not touch the database")

    app = create_app(engine=ExplodingEngine())
    app.dependency_overrides[get_openrouter_adapter] = lambda: stub_adapter

    # Bypass lifespan (which does init the DB) so only the request path is exercised.
    client = TestClient(app)
    response = client.get("/api/health")
    # If the route depended on a session, resolving it would hit ExplodingEngine and 500.
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
