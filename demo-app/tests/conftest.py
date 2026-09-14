"""Shared fixtures — the pattern for future test modules (spec: Testing Decisions).

Two reusable seams:
- ``engine`` / ``session``: a temporary SQLite database, isolated per test.
- ``stub_adapter``: a canned OpenRouter adapter, so tests never hit the network.
"""

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from api.app import create_app
from core.adapters.openrouter import DEFAULT_MAX_TOKENS, get_openrouter_adapter
from core.db import init_db, make_engine
from core.services.pdf_processing import page_image_filename


def seed_page_images(base_dir: Path, n_pages: int, size=(64, 64)) -> list[Path]:
    """Write ``n_pages`` small white native-raster PNGs where render-on-demand expects them —
    ``<base_dir>/page_NNNN.png`` (ticket 05). A Page whose ``image_path`` lives under
    ``base_dir`` then renders on demand (the runner derives the drawing dir from the image
    path's parent and reads the native raster from it), so run-path tests exercise a real
    render instead of a fake ``/tmp`` path. Returns the written paths, newest convention shape,
    matching real ingestion's per-drawing directory layout."""
    base_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for page_number in range(1, n_pages + 1):
        path = base_dir / page_image_filename(page_number)
        Image.new("RGB", size, "white").save(path)
        paths.append(path)
    return paths


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
        self,
        image_path: Path,
        model: str,
        prompt: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ) -> dict:
        self.calls.append(
            {
                "image_path": image_path,
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
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
def sample_pdf(tmp_path) -> Path:
    """A small 2-page PDF with distinct page sizes, drawn on the fly.

    Distinct sizes let tests assert that each Page's own pixel dimensions are
    persisted rather than a shared constant.
    """
    pdf_path = tmp_path / "sample.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=612, height=792)  # US Letter portrait
        doc.new_page(width=792, height=612)  # US Letter landscape
        doc.save(pdf_path)
    return pdf_path


@pytest.fixture
def sample_image(tmp_path) -> Path:
    """A single landscape PNG larger than the downsample long edge, drawn on the fly.

    Non-square so tests can assert the native pixel dimensions are persisted, and big
    enough (2000×1500) that ingest actually downsamples it rather than upscaling.
    """
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (2000, 1500), color="white").save(image_path)
    return image_path


@pytest.fixture
def app(engine, stub_adapter):
    app = create_app(engine=engine)
    app.dependency_overrides[get_openrouter_adapter] = lambda: stub_adapter
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client
