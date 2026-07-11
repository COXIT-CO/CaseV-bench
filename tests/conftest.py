"""Shared fixtures — the pattern for future test modules (spec: Testing Decisions).

Two reusable seams:
- ``engine`` / ``session``: a temporary SQLite database, isolated per test.
- ``stub_adapter``: a canned OpenRouter adapter, so tests never hit the network.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from adapters.openrouter import get_openrouter_adapter
from db import init_db, make_engine
from web.app import create_app


class StubOpenRouterAdapter:
    """In-memory OpenRouter stub returning canned responses keyed by model slug.

    Shapes its return like the real API (``choices[0].message.content``) and records
    each call so tests can assert on what was requested.
    """

    def __init__(self, responses: dict[str, str] | None = None, default: str = "{}"):
        self.responses = responses or {}
        self.default = default
        self.calls: list[dict] = []

    def send_image_prompt(
        self, image_path: Path, model: str, prompt: str, prefill_json: bool = False
    ) -> dict:
        self.calls.append(
            {
                "image_path": image_path,
                "model": model,
                "prompt": prompt,
                "prefill_json": prefill_json,
            }
        )
        content = self.responses.get(model, self.default)
        return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def stub_adapter():
    return StubOpenRouterAdapter()


@pytest.fixture
def app(engine, stub_adapter):
    app = create_app(engine=engine)
    app.dependency_overrides[get_openrouter_adapter] = lambda: stub_adapter
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client
