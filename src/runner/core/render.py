import io
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

_POINTS_PER_INCH = 72.0


@dataclass(frozen=True, slots=True)
class Transform:
    image_width: int
    image_height: int

    def to_pixels(self, x: float, y: float) -> tuple[float, float]:
        return x * self.image_width, y * self.image_height


@dataclass(frozen=True, slots=True)
class RenderedPage:
    png_bytes: bytes
    requested_long_edge: int
    long_edge: int
    effective_dpi: float
    transform: Transform

    @property
    def width(self) -> int:
        return self.transform.image_width

    @property
    def height(self) -> int:
        return self.transform.image_height


class PageRenderer:
    """Renders a PDF page to PNG at a target long edge, backed by an
    on-disk cache under cache_dir so repeated runs against the same
    drawing/page/size don't re-rasterize."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir

    def _cache_path(self, drawing: str, page_number: int, target_long_edge: int) -> Path:
        return self._cache_dir / drawing / f"p{page_number:04d}_{target_long_edge}.png"

    @staticmethod
    def _page_long_edge_pt(document: pymupdf.Document, page_number: int) -> float:
        rect = document[page_number - 1].rect
        return float(max(rect.width, rect.height))

    @staticmethod
    def _effective_dpi(long_edge: int, page_long_edge_pt: float) -> float:
        return long_edge / (page_long_edge_pt / _POINTS_PER_INCH)

    @staticmethod
    def _native_pixel_cap(page: pymupdf.Page, page_long_edge_pt: float) -> int | None:
        best_pixels_per_point = 0.0
        for image_info in page.get_image_info(xrefs=True):
            bbox = image_info.get("bbox")
            width_px = image_info.get("width")
            height_px = image_info.get("height")
            if not bbox or not width_px or not height_px:
                continue
            bbox_width = bbox[2] - bbox[0]
            bbox_height = bbox[3] - bbox[1]
            if bbox_width <= 0 or bbox_height <= 0:
                continue
            pixels_per_point = max(width_px / bbox_width, height_px / bbox_height)
            best_pixels_per_point = max(best_pixels_per_point, pixels_per_point)

        if not best_pixels_per_point:
            return None
        return max(1, round(best_pixels_per_point * page_long_edge_pt))

    @staticmethod
    def render_page(
        document: pymupdf.Document,
        page_number: int,
        target_long_edge: int,
    ) -> RenderedPage:
        page = document[page_number - 1]
        page_long_edge_pt = PageRenderer._page_long_edge_pt(document, page_number)

        native_cap = PageRenderer._native_pixel_cap(page, page_long_edge_pt)
        long_edge = max(1, min(target_long_edge, native_cap or target_long_edge))

        scale = long_edge / page_long_edge_pt
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))

        return RenderedPage(
            png_bytes=pixmap.tobytes("png"),
            requested_long_edge=target_long_edge,
            long_edge=long_edge,
            effective_dpi=PageRenderer._effective_dpi(long_edge, page_long_edge_pt),
            transform=Transform(image_width=pixmap.width, image_height=pixmap.height),
        )

    def render(
        self,
        document: pymupdf.Document,
        drawing: str,
        page_number: int,
        target_long_edge: int,
    ) -> RenderedPage:
        path = self._cache_path(drawing, page_number, target_long_edge)
        if path.exists():
            png_bytes = path.read_bytes()
            with Image.open(io.BytesIO(png_bytes)) as cached_image:
                width, height = cached_image.size
            long_edge = max(width, height)
            return RenderedPage(
                png_bytes=png_bytes,
                requested_long_edge=target_long_edge,
                long_edge=long_edge,
                effective_dpi=self._effective_dpi(
                    long_edge, self._page_long_edge_pt(document, page_number)
                ),
                transform=Transform(image_width=width, image_height=height),
            )

        rendered = self.render_page(document, page_number, target_long_edge)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rendered.png_bytes)
        return rendered
