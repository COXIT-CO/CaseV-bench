from pathlib import Path

import pymupdf

from core.config import settings

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
