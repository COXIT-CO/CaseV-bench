from typing import Sequence

from ._types import Rect, Size


def to_pixels(bbox: Sequence[float], size: Size) -> Rect:
    """Scale a 0–1 box to pixels, ordering the corners so the result is always drawable.

    An inverted box is normalized rather than rejected: it is a wrong prediction worth looking
    at, and Pillow refuses to draw one at all. Nothing is clamped to the canvas — a box reaching
    past the edge is clipped by Pillow, which keeps an out-of-range prediction looking
    out-of-range instead of hugging the border like a legitimate edge detection.
    """
    width, height = size
    x_min, y_min, x_max, y_max = (float(value) for value in bbox)
    left, right = sorted((x_min * width, x_max * width))
    top, bottom = sorted((y_min * height, y_max * height))
    return round(left), round(top), round(right), round(bottom)


def line_width_for(size: Size) -> int:
    """A stroke that stays visible on a 3000px sheet and does not swallow a thumbnail."""
    return max(1, round(min(size) / 400))


def font_size_for(size: Size) -> int:
    """Label size from the same measurement, floored where text stops being readable at all."""
    return max(11, round(min(size) / 55))
