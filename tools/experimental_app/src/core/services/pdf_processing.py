import os
import threading
from pathlib import Path

import pymupdf
from PIL import Image

from core.config import settings
from core.utils import downsample

# Production default under the single data root; callers inject a temp dir in tests (ADR-0014).
DEFAULT_OUTPUT_DIR = settings.output_root
DEFAULT_DPI = 300


def page_image_filename(page_number: int) -> str:
    """The on-disk name of a page's full-resolution raster (1-based). The single owner of the
    convention, so the image-ingest path (``DrawingService._store_image_page``) writes the
    same shape this renderer emits and the two can't drift."""
    return f"page_{page_number:04d}.png"


class PDFProcessingService:
    def __init__(self, dpi: int = DEFAULT_DPI, output_dir: Path = DEFAULT_OUTPUT_DIR):
        self.dpi = dpi
        self.output_dir = output_dir

    def extract_images(self, pdf_path: Path, output_dir: Path = None) -> list[Path]:
        pdf_dir = output_dir if output_dir else self.output_dir / pdf_path.stem
        pdf_dir.mkdir(parents=True, exist_ok=True)

        image_paths = []
        with pymupdf.open(pdf_path) as doc:
            for page_index, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=self.dpi)
                image_path = pdf_dir / page_image_filename(page_index)
                pix.save(image_path)
                image_paths.append(image_path)

        return image_paths

    def native_page_dims(self, pdf_path: Path) -> list[tuple[float, float]]:
        """Each page's native point dimensions (``page.rect`` width/height, in PDF points) —
        the 72-DPI frame where 1 px = 1 PDF point. Captured at ingest and stored on the Page
        so the native location-GT importer normalizes boxes without touching the PDF (ADR
        0022). DPI-independent by design: it is the source page's own size, not the render's.
        """
        with pymupdf.open(pdf_path) as doc:
            return [(page.rect.width, page.rect.height) for page in doc]


def _render_cache_dir(
    drawing_dir: Path, source_path: str | None, dpi: int, downsample_px: int | None
) -> Path:
    """The per-``(dpi, downsample)`` cache dir under a Drawing's directory (ADR 0018). Living
    *inside* ``drawing_dir`` means the existing delete-cascade cleanup (``remove_tree`` of the
    drawing dir) sweeps these renders too. A PDF-ingested Drawing keys on both dpi and
    downsample; an image-ingested one ignores dpi (there is no PDF to re-rasterize) so it keys
    on downsample alone, sharing one render across every DPI."""
    edge = downsample_px if downsample_px is not None else "full"
    if source_path is None:
        return drawing_dir / f"render_native_ds{edge}"
    return drawing_dir / f"render_dpi{dpi}_ds{edge}"


def render_run_page(
    drawing_dir: Path,
    page_number: int,
    source_path: str | None,
    dpi: int,
    downsample_px: int | None,
) -> Path:
    """Render (or reuse a cached render of) one Page at a Run's ``(dpi, downsample_px)`` and
    return the image path handed to the Model (ADR 0018).

    A PDF-ingested Drawing (``source_path`` set) is re-rasterized from the retained PDF at the
    chosen ``dpi`` — this is what makes DPI genuinely effective. An image-ingested Drawing
    (``source_path`` None) has no PDF, so ``dpi`` is ignored and it renders from its stored
    full-resolution native raster (``<drawing_dir>/page_NNNN.png``). Either way the result is
    downsampled to ``downsample_px`` (``None`` = full resolution, no downsample). The render is
    cached keyed by the knobs so re-runs reuse it and drawing-delete cleans it up.

    A Run's models fan out in parallel over one Drawing (ADR 0006), so several workers can
    render the same page at once. The cache is therefore published **atomically**: each render
    writes to scratch names of its own and ``os.replace``s the finished image into ``dest``,
    because the lookup below is an ``exists()`` check and a half-written PNG satisfies it. The
    cost of losing that race is a duplicated render; the cost of losing it the other way is a
    worker handed a truncated file — which the location path opens back to draw its overlay
    on, failing the whole Run."""
    cache_dir = _render_cache_dir(drawing_dir, source_path, dpi, downsample_px)
    dest = cache_dir / page_image_filename(page_number)
    if dest.exists():
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)

    def scratch(prefix: str) -> Path:
        """A working file only this render owns. Qualified by thread so two workers rendering
        the same page never share one — otherwise each would read, overwrite, or unlink the
        other's half-written raster."""
        return (
            cache_dir
            / f"{prefix}_{os.getpid()}_{threading.get_ident()}_{page_image_filename(page_number)}"
        )

    # The full-resolution raster for this page, per source type: a PDF is re-rasterized at the
    # chosen dpi; an image drawing reuses its stored native raster (dpi ignored — no PDF).
    if source_path is not None:
        raw = pdf_scratch = scratch("raw")
        with pymupdf.open(source_path) as doc:
            doc[page_number - 1].get_pixmap(dpi=dpi).save(raw)
    else:
        raw = drawing_dir / page_image_filename(page_number)
        pdf_scratch = None

    # One tail for both sources: full resolution (no downsample) copies the raster through;
    # otherwise it is downsampled to the chosen long edge.
    pending = scratch("pending")
    try:
        if downsample_px is None:
            with Image.open(raw) as image:
                image.convert("RGB").save(pending)
        else:
            downsample(raw, pending, downsample_px)
        os.replace(pending, dest)
    finally:
        # A render that raised leaves no litter behind in the shared cache dir.
        pending.unlink(missing_ok=True)
        if pdf_scratch is not None:
            pdf_scratch.unlink(missing_ok=True)
    return dest
