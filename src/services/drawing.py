"""Drawing ingestion — turn an uploaded PDF into a Drawing with its Pages.

Each page is rendered and downsampled exactly once at ingest and cached on disk;
runs reuse the cached image (spec: Drawings & ingestion). Reuses the existing
``PDFProcessingService`` render and ``utils.downsample`` rather than duplicating
either.
"""

from pathlib import Path

from PIL import Image
from sqlmodel import Session

from models.drawing import Drawing, Page
from services.pdf_processing import PDFProcessingService
from utils import downsample

DEFAULT_CACHE_ROOT = Path("data/drawings")


class DrawingService:
    def __init__(
        self,
        session: Session,
        pdf_service: PDFProcessingService | None = None,
        cache_root: Path = DEFAULT_CACHE_ROOT,
    ):
        self.session = session
        self.pdf_service = pdf_service or PDFProcessingService()
        self.cache_root = Path(cache_root)

    def ingest(self, pdf_path: Path, name: str) -> Drawing:
        """Create a Drawing and one Page per PDF page, caching a downsampled image."""
        drawing = Drawing(name=name)
        self.session.add(drawing)
        self.session.commit()
        self.session.refresh(drawing)

        page_dir = self.cache_root / str(drawing.id)
        rendered = self.pdf_service.extract_images(pdf_path, output_dir=page_dir)

        for page_number, full_res in enumerate(rendered, start=1):
            # Record the full-resolution render's dimensions: imported COCO boxes
            # (ticket 10) are annotated against the real page raster, not our
            # model-facing downsample, so normalizing by these dims lands in 0-1.
            with Image.open(full_res) as image:
                width_px, height_px = image.size
            cached = full_res.with_stem(f"{full_res.stem}_downsampled")
            downsample(full_res, cached)
            self.session.add(
                Page(
                    drawing_id=drawing.id,
                    page_number=page_number,
                    image_path=str(cached),
                    width_px=width_px,
                    height_px=height_px,
                )
            )

        self.session.commit()
        self.session.refresh(drawing)
        return drawing
