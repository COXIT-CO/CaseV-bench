from pathlib import Path

import pymupdf

DEFAULT_OUTPUT_DIR = Path("data/output")
DEFAULT_DPI = 300


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
                image_path = pdf_dir / f"page_{page_index:04d}.png"
                pix.save(image_path)
                image_paths.append(image_path)

        return image_paths
