"""Per-run render-on-demand seam (ticket 05, ADR 0018).

DPI and downsample become genuinely effective per Run: ingest retains the source PDF, and a
Run renders each Page on demand at its ``(dpi, downsample_px)`` — handing *that* image to the
Model, not the fixed ingest downsample. These drive the same ``OpenRouterAdapter`` stub as the
other run tests and assert the external behaviour: the image path handed to the adapter is the
Run's ``(dpi, downsample)`` variant, and an image-ingested Drawing (no PDF) ignores DPI.
"""

from pathlib import Path

from PIL import Image

from core.models.prompt import Task
from core.services.drawing import DrawingService
from core.services.pdf_processing import PDFProcessingService
from core.services.prompt import PromptService
from core.services.run import RunKnobs, RunService

MODEL = "anthropic/claude-sonnet-4.5"
COUNT_JSON = '{"cabinet": 3, "countertop": 1, "elevation": 2, "elevation_callout": 0}'


def _ingest_pdf(session, sample_pdf, tmp_path) -> tuple:
    # Ingest at a low DPI to keep the fixture render fast; the Run re-renders from the PDF.
    service = DrawingService(
        session,
        pdf_service=PDFProcessingService(dpi=72),
        cache_root=tmp_path / "drawings",
    )
    drawing = service.ingest(sample_pdf, name="sample")
    prompt = PromptService(session).create(Task.counting, "default", "count them")
    return drawing, prompt


def _ingest_image(session, sample_image, tmp_path) -> tuple:
    service = DrawingService(session, cache_root=tmp_path / "drawings")
    drawing = service.ingest(sample_image, name="photo")
    prompt = PromptService(session).create(Task.counting, "default", "count them")
    return drawing, prompt


def _launch(session, stub_adapter, prompt, drawing, knobs) -> None:
    stub_adapter.responses = {MODEL: COUNT_JSON}
    RunService(session, stub_adapter, knobs=knobs).launch(
        Task.counting, prompt.id, drawing.id, [MODEL]
    )


def test_pdf_run_hands_model_the_dpi_downsample_variant(
    session, stub_adapter, sample_pdf, tmp_path
):
    # A PDF-ingested Drawing retains its source PDF so a Run can re-rasterize on demand.
    drawing, prompt = _ingest_pdf(session, sample_pdf, tmp_path)
    assert drawing.source_path is not None and Path(drawing.source_path).exists()

    knobs = RunKnobs(dpi=150, downsample_px=800)
    _launch(session, stub_adapter, prompt, drawing, knobs)

    sent = Path(stub_adapter.calls[0]["image_path"])
    # The Model got the Run's (dpi, downsample) variant, cached under the drawing dir…
    assert sent.exists()
    assert sent.parent.name == "render_dpi150_ds800"
    # …not the fixed ingest downsample the Page recorded.
    page_image = drawing.pages[0].image_path
    assert str(sent) != page_image
    # The downsample knob is effective: the variant's long edge is the chosen 800px.
    with Image.open(sent) as image:
        assert max(image.size) == 800


def test_pdf_dpi_is_effective_on_full_resolution_renders(
    session, stub_adapter, sample_pdf, tmp_path
):
    # With downsample off (full resolution), a higher DPI yields a genuinely larger raster —
    # the point of retaining the PDF and re-rendering rather than reusing the ingest image.
    drawing, prompt = _ingest_pdf(session, sample_pdf, tmp_path)

    _launch(
        session, stub_adapter, prompt, drawing, RunKnobs(dpi=72, downsample_px=None)
    )
    low_dpi = Path(stub_adapter.calls[-1]["image_path"])
    _launch(
        session, stub_adapter, prompt, drawing, RunKnobs(dpi=200, downsample_px=None)
    )
    high_dpi = Path(stub_adapter.calls[-1]["image_path"])

    with Image.open(low_dpi) as low, Image.open(high_dpi) as high:
        assert max(high.size) > max(low.size)


def test_image_drawing_ignores_dpi(session, stub_adapter, sample_image, tmp_path):
    # An image-ingested Drawing has no PDF: DPI is ignored, so two Runs differing only in DPI
    # (full resolution) hand the Model the identical native-raster render.
    drawing, prompt = _ingest_image(session, sample_image, tmp_path)
    assert drawing.source_path is None

    _launch(
        session, stub_adapter, prompt, drawing, RunKnobs(dpi=72, downsample_px=None)
    )
    at_72 = Path(stub_adapter.calls[-1]["image_path"])
    _launch(
        session, stub_adapter, prompt, drawing, RunKnobs(dpi=600, downsample_px=None)
    )
    at_600 = Path(stub_adapter.calls[-1]["image_path"])

    # DPI leaves no trace on an image drawing's render key, and the pixels are identical.
    assert at_72.parent.name == at_600.parent.name == "render_native_dsfull"
    with Image.open(at_72) as a, Image.open(at_600) as b:
        assert a.size == b.size == (2000, 1500)


def test_image_drawing_honours_downsample_knob(
    session, stub_adapter, sample_image, tmp_path
):
    # DPI is ignored for an image drawing, but the downsample knob still applies to its native
    # raster (2000×1500 → long edge 1000).
    drawing, prompt = _ingest_image(session, sample_image, tmp_path)

    _launch(
        session, stub_adapter, prompt, drawing, RunKnobs(dpi=300, downsample_px=1000)
    )
    sent = Path(stub_adapter.calls[-1]["image_path"])
    assert sent.parent.name == "render_native_ds1000"
    with Image.open(sent) as image:
        assert max(image.size) == 1000
