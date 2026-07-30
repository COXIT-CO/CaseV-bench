"""Shared fixtures — the pattern for future test modules (spec: Testing Decisions).

Reusable seams:
- ``engine`` / ``session``: a temporary SQLite database, isolated per test.
- ``stub_adapter``: a canned OpenRouter adapter, so tests never hit the network.
- **Location seeding** (spec-drop-counting: Seams): a location prompt fixture plus
  factories for a seeded location Run and seeded location ground truth. Task-neutral
  behaviour — background execution, orphan reconciliation, render-on-demand, the runs /
  library / leaderboard APIs, run execution, the CLI — is exercised through *these*
  rather than through a counting Run, which was only ever the cheapest thing to build.
  One definition here replaces the near-identical copy each of those modules carried.
"""

import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from api.app import create_app
from api.deps import get_run_service
from core.adapters.openrouter import DEFAULT_MAX_TOKENS, get_openrouter_adapter
from core.db import init_db, make_engine
from core.models.drawing import Drawing, Page
from core.models.prompt import Prompt, Task
from core.models.run import Result, Run, RunStatus
from core.services.location_ground_truth import LocationGroundTruthService
from core.services.pdf_processing import page_image_filename
from core.services.prompt import PromptService
from core.services.run import RunKnobs, RunService


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


def make_drawing_with_pages(
    session: Session, native_dims: list[tuple[int, int]], name: str = "d"
) -> Drawing:
    """A Drawing with one Page per native ``(width_pt, height_pt)``, numbered from 1.

    Each Page's full-resolution pixel dims are set to a different (larger) frame than its
    native point dims, so a test that passes when code normalizes by the **native** dims (the
    frame the expert labeled against — ADR 0022) would fail if it wrongly normalized by the
    pixel dims.
    """
    drawing = Drawing(name=name)
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    for page_number, (native_w, native_h) in enumerate(native_dims, start=1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=f"page_{page_number:04d}_downsampled.png",
                width_px=native_w * 4,
                height_px=native_h * 4,
                native_width_pt=float(native_w),
                native_height_pt=float(native_h),
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


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


# --- Location seeding -------------------------------------------------------------------
#
# The shared seam task-neutral modules build their fixtures from (spec-drop-counting: Seams).
# Everything below is location because that is the task the benchmark actually runs; nothing
# here is specific to the behaviour any one module covers.

# The frame a seeded location page is labeled against: a square 100pt page, so a GT object's
# absolute pixel box divides straight through to its normalized corners and the arithmetic
# stays readable in the test that asserts on it.
LOCATION_PAGE_PT = 100.0

# A model response detecting one cabinet, paired with the ground-truth document asserting
# exactly that box (10/100 → 0.1, 40/100 → 0.4). Paired deliberately: a Result answering
# LOCATION_BOXES_JSON scores a perfect 1.0 against LOCATION_GT_OBJECTS, so a test that wants
# an imperfect score varies one side rather than inventing both.
LOCATION_BOXES_JSON = json.dumps(
    [
        {
            "label": "cabinet",
            "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.4},
        }
    ]
)
LOCATION_GT_OBJECTS = {
    "objects": [
        {
            "id": "a",
            "category": "cabinet",
            "page": 1,
            "bbox": {"x": 10, "y": 10, "width": 30, "height": 30},
        }
    ]
}


def seed_location_drawing(session: Session, n_pages: int = 1, name="sample") -> Drawing:
    """A Drawing whose Pages point at real native rasters, ready for a location Run.

    The rasters land under ``<data dir>/drawings/<drawing id>/`` — the layout real ingestion
    produces and the one render-on-demand derives from a Page's ``image_path`` parent — so a
    Run hands the Model a genuinely rendered image rather than a path that does not exist. The
    data dir is the engine's own, which is the per-test ``tmp_path``.

    Native point dims are the square ``LOCATION_PAGE_PT`` frame ``LOCATION_GT_OBJECTS`` is
    authored against, so the GT factory can import against this Drawing unchanged.
    """
    data_dir = Path(session.get_bind().url.database).parent
    drawing = Drawing(name=name)
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    images = seed_page_images(data_dir / "drawings" / str(drawing.id), n_pages)
    for page_number, image in enumerate(images, start=1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=str(image),
                width_px=100,
                height_px=100,
                native_width_pt=LOCATION_PAGE_PT,
                native_height_pt=LOCATION_PAGE_PT,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def seed_location_drawing_id(engine, n_pages: int = 1, name="sample") -> int:
    """``seed_location_drawing`` for the HTTP seam, which holds no Session of its own — opens
    one against the engine and returns just the Drawing id, the only handle a request needs.
    """
    with Session(engine) as session:
        return seed_location_drawing(session, n_pages=n_pages, name=name).id


@pytest.fixture
def location_prompt(session) -> Prompt:
    """The location ``default`` family's first version — the prompt a seeded Run pins.

    Reuses the app's seeded row when the lifespan has already run, so a test that mixes this
    fixture with the HTTP seam pins the same Prompt the launch form offers rather than a
    second family that would collide on ``(task, family, version)``.
    """
    service = PromptService(session)
    return service.latest(Task.location, "default") or service.create(
        Task.location, "default", "find them"
    )


@pytest.fixture
def seed_location_run():
    """Factory for a location Run row (plus one Result per model), inserted directly.

    For tests that need a Run to *exist* — delete collateral, an orphan to reconcile — with no
    model call and no execution. Takes the session so it serves both the service seam and the
    HTTP seam (which opens its own sessions against the engine). ``status`` defaults to
    ``done`` since a bare seeded Run stands in for a finished one; the FK ids are unenforced
    under SQLite, so a caller with no real Drawing can pass any.
    """

    def _seed(
        session: Session,
        prompt_id: int,
        drawing_id: int,
        models: tuple[str, ...] = (),
        status: RunStatus = RunStatus.done,
    ) -> Run:
        knobs = RunKnobs()
        run = Run(
            task=Task.location,
            prompt_id=prompt_id,
            drawing_id=drawing_id,
            status=status,
            dpi=knobs.dpi,
            downsample_px=knobs.downsample_px,
            max_tokens=knobs.max_tokens,
            temperature=knobs.temperature,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        for model in models:
            session.add(Result(run_id=run.id, model=model))
        session.commit()
        session.refresh(run)
        return run

    return _seed


@pytest.fixture
def seed_location_gt():
    """Factory importing a Drawing's location ground truth through the real importer.

    Defaults to ``LOCATION_GT_OBJECTS`` — the answer key ``LOCATION_BOXES_JSON`` scores
    perfectly against — so a test that only needs "this Drawing is scored" says exactly that.
    """

    def _seed(session: Session, drawing_id: int, document=LOCATION_GT_OBJECTS):
        return LocationGroundTruthService(session).import_objects(drawing_id, document)

    return _seed


@pytest.fixture
def overlay_root(tmp_path) -> Path:
    """Where a location Run under test writes its prediction-overlay PNGs.

    A location Run renders one overlay per Prediction; left alone a ``RunService`` writes them
    to the configured production root (ADR-0014), so every test that executes one has to
    inject a temp root. One definition, so the service seam stops repeating the same
    ``tmp_path / "overlays"`` in every launch.
    """
    return tmp_path / "overlays"


@pytest.fixture
def temp_overlay_run_service(app, engine, stub_adapter, overlay_root) -> Path:
    """Override the launch route's ``RunService`` with one writing overlays under ``tmp_path``.

    The HTTP-seam counterpart to ``overlay_root``: an API test never constructs the service
    itself, so redirecting its writes has to happen through the dependency. Required by any
    API test that launches a Run, or it litters the repo's data directory.
    """

    def _run_service():
        with Session(engine) as session:
            yield RunService(session, stub_adapter, overlay_root=overlay_root)

    app.dependency_overrides[get_run_service] = _run_service
    return overlay_root
