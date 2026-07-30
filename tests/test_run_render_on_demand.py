"""Per-run render-on-demand seam (ticket 05, ADR 0018).

DPI and downsample become genuinely effective per Run: ingest retains the source PDF, and a
Run renders each Page on demand at its ``(dpi, downsample_px)`` — handing *that* image to the
Model, not the fixed ingest downsample. These drive the same ``OpenRouterAdapter`` stub as the
other run tests and assert the external behaviour: the image path handed to the adapter is the
Run's ``(dpi, downsample)`` variant, and an image-ingested Drawing (no PDF) ignores DPI.

One test covers the render *cache* rather than the knobs: a Run's models fan out in parallel
over the same Drawing (ADR 0006), so the cache has to be published atomically or a worker is
handed a half-written PNG. It lives here because ``render_run_page`` is this module's seam.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conftest import LOCATION_BOXES_JSON
from PIL import Image

from core.models.prompt import Task
from core.services import pdf_processing
from core.services.drawing import DrawingService
from core.services.pdf_processing import PDFProcessingService, render_run_page
from core.services.run import RunKnobs, RunService

MODEL = "anthropic/claude-sonnet-4.5"


def _ingest_pdf(session, sample_pdf, tmp_path):
    # Ingest at a low DPI to keep the fixture render fast; the Run re-renders from the PDF.
    service = DrawingService(
        session,
        pdf_service=PDFProcessingService(dpi=72),
        cache_root=tmp_path / "drawings",
    )
    return service.ingest(sample_pdf, name="sample")


def _ingest_image(session, sample_image, tmp_path):
    service = DrawingService(session, cache_root=tmp_path / "drawings")
    return service.ingest(sample_image, name="photo")


def _launch(session, stub_adapter, prompt, drawing, knobs, overlay_root) -> None:
    stub_adapter.responses = {MODEL: LOCATION_BOXES_JSON}
    RunService(session, stub_adapter, knobs=knobs, overlay_root=overlay_root).launch(
        Task.location, prompt.id, drawing.id, [MODEL]
    )


def test_pdf_run_hands_model_the_dpi_downsample_variant(
    session, stub_adapter, location_prompt, sample_pdf, tmp_path, overlay_root
):
    # A PDF-ingested Drawing retains its source PDF so a Run can re-rasterize on demand.
    drawing = _ingest_pdf(session, sample_pdf, tmp_path)
    assert drawing.source_path is not None and Path(drawing.source_path).exists()

    knobs = RunKnobs(dpi=150, downsample_px=800)
    _launch(session, stub_adapter, location_prompt, drawing, knobs, overlay_root)

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
    session, stub_adapter, location_prompt, sample_pdf, tmp_path, overlay_root
):
    # With downsample off (full resolution), a higher DPI yields a genuinely larger raster —
    # the point of retaining the PDF and re-rendering rather than reusing the ingest image.
    drawing = _ingest_pdf(session, sample_pdf, tmp_path)

    _launch(
        session,
        stub_adapter,
        location_prompt,
        drawing,
        RunKnobs(dpi=72, downsample_px=None),
        overlay_root,
    )
    low_dpi = Path(stub_adapter.calls[-1]["image_path"])
    _launch(
        session,
        stub_adapter,
        location_prompt,
        drawing,
        RunKnobs(dpi=200, downsample_px=None),
        overlay_root,
    )
    high_dpi = Path(stub_adapter.calls[-1]["image_path"])

    with Image.open(low_dpi) as low, Image.open(high_dpi) as high:
        assert max(high.size) > max(low.size)


def test_image_drawing_ignores_dpi(
    session, stub_adapter, location_prompt, sample_image, tmp_path, overlay_root
):
    # An image-ingested Drawing has no PDF: DPI is ignored, so two Runs differing only in DPI
    # (full resolution) hand the Model the identical native-raster render.
    drawing = _ingest_image(session, sample_image, tmp_path)
    assert drawing.source_path is None

    _launch(
        session,
        stub_adapter,
        location_prompt,
        drawing,
        RunKnobs(dpi=72, downsample_px=None),
        overlay_root,
    )
    at_72 = Path(stub_adapter.calls[-1]["image_path"])
    _launch(
        session,
        stub_adapter,
        location_prompt,
        drawing,
        RunKnobs(dpi=600, downsample_px=None),
        overlay_root,
    )
    at_600 = Path(stub_adapter.calls[-1]["image_path"])

    # DPI leaves no trace on an image drawing's render key, and the pixels are identical.
    assert at_72.parent.name == at_600.parent.name == "render_native_dsfull"
    with Image.open(at_72) as a, Image.open(at_600) as b:
        assert a.size == b.size == (2000, 1500)


def test_image_drawing_honours_downsample_knob(
    session, stub_adapter, location_prompt, sample_image, tmp_path, overlay_root
):
    # DPI is ignored for an image drawing, but the downsample knob still applies to its native
    # raster (2000×1500 → long edge 1000).
    drawing = _ingest_image(session, sample_image, tmp_path)

    _launch(
        session,
        stub_adapter,
        location_prompt,
        drawing,
        RunKnobs(dpi=300, downsample_px=1000),
        overlay_root,
    )
    sent = Path(stub_adapter.calls[-1]["image_path"])
    assert sent.parent.name == "render_native_ds1000"
    with Image.open(sent) as image:
        assert max(image.size) == 1000


def test_render_in_progress_is_never_handed_to_another_worker(
    session, sample_pdf, tmp_path, monkeypatch
):
    """A Run's models fan out in parallel over the *same* Drawing, so two workers can render
    the same page at the same knobs at once. The second must never be handed the first's
    half-written file: the cache lookup is an ``exists()`` check, which a partial PNG
    satisfies. A location Run opens the render back to draw its overlay on, so a truncated
    one there raises and fails the whole Run.

    The interleaving is forced rather than raced: the first worker's image write is held
    open mid-flight while the second asks for the same page.
    """
    drawing = _ingest_pdf(session, sample_pdf, tmp_path)
    page = drawing.pages[0]
    drawing_dir = Path(page.image_path).parent
    source_path = drawing.source_path

    real_downsample = pdf_processing.downsample
    mid_write = threading.Event()
    second_done = threading.Event()
    started = []

    def held_downsample(src, dest, long_edge):
        """Stand in for a real write caught mid-flight: leave a PNG header at the destination
        and hold there, so anything checking for the file finds an incomplete one."""
        first_call = not started
        started.append(dest)
        if first_call:
            Path(dest).write_bytes(b"\x89PNG\r\n\x1a\n")
            mid_write.set()
            assert second_done.wait(timeout=10)
        return real_downsample(src, dest, long_edge)

    monkeypatch.setattr(pdf_processing, "downsample", held_downsample)

    def render_and_read() -> tuple[int, int]:
        """Render the page and immediately read the image back, as the location run path
        does when it draws its overlay on what it was handed."""
        path = render_run_page(drawing_dir, page.page_number, source_path, 300, 1568)
        with Image.open(path) as image:
            image.load()  # decodes the pixel data, so a truncated file raises here
            return image.size

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(render_and_read)
        assert mid_write.wait(timeout=10)
        second = pool.submit(render_and_read)
        try:
            second_size = second.result(timeout=10)
        finally:
            second_done.set()
        assert first.result(timeout=10) == second_size
