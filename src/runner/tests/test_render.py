import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from core.render import PageRenderer


def _write_pdf(path: Path, width: float = 792, height: float = 612) -> None:
    document = pymupdf.open()
    document.new_page(width=width, height=height)
    document.save(path)
    document.close()


class TestPageRenderer:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.pdf_path = tmp_path / "d.pdf"
        _write_pdf(self.pdf_path)

    def test_render_page_hits_the_requested_long_edge(self) -> None:
        with pymupdf.open(self.pdf_path) as document:
            rendered = PageRenderer.render_page(document, 1, target_long_edge=800)

        assert rendered.long_edge == 800
        assert max(rendered.width, rendered.height) == 800
        with Image.open(io.BytesIO(rendered.png_bytes)) as image:
            assert image.size == (rendered.width, rendered.height)

    def test_render_reuses_the_cache_file(self) -> None:
        renderer = PageRenderer(self.tmp_path / "cache")

        with pymupdf.open(self.pdf_path) as document:
            first = renderer.render(document, "d", 1, target_long_edge=400)
            cache_file = self.tmp_path / "cache" / "d" / "p0001_400.png"
            assert cache_file.exists()

            cached_bytes_before = cache_file.read_bytes()
            second = renderer.render(document, "d", 1, target_long_edge=400)

        assert second.png_bytes == first.png_bytes == cached_bytes_before
