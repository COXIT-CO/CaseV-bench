from __future__ import annotations

from typing import Tuple

from PIL import Image


def _normalize_box(box) -> tuple[float, float, float, float]:
    left, top, right, bottom = (float(v) for v in box)
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top
    left = max(0.0, min(1000.0, left))
    top = max(0.0, min(1000.0, top))
    right = max(0.0, min(1000.0, right))
    bottom = max(0.0, min(1000.0, bottom))
    return left, top, right, bottom


def normalized_box_to_pixels(
    box,
    width: int,
    height: int,
    pad_pct: float = 0.02,
) -> Tuple[int, int, int, int]:
    """Convert a 0..1000 normalized [left, top, right, bottom] box to clamped
    pixel coordinates on an image of the given size, with a small padding
    margin added around it so cabinet/countertop geometry sitting right at
    the elevation box's edge isn't clipped off by the crop."""
    left, top, right, bottom = _normalize_box(box)

    x0 = left / 1000.0 * width
    y0 = top / 1000.0 * height
    x1 = right / 1000.0 * width
    y1 = bottom / 1000.0 * height

    pad_x = (x1 - x0) * pad_pct
    pad_y = (y1 - y0) * pad_pct
    x0 -= pad_x
    y0 -= pad_y
    x1 += pad_x
    y1 += pad_y

    x0 = max(0, int(round(x0)))
    y0 = max(0, int(round(y0)))
    x1 = min(width, int(round(x1)))
    y1 = min(height, int(round(y1)))
    x1 = max(x0 + 1, x1)
    y1 = max(y0 + 1, y1)
    return x0, y0, x1, y1


def crop_image_to_box(
    image_path: str,
    box,
    output_path: str,
    pad_pct: float = 0.02,
) -> tuple[str, tuple[int, int, int, int], tuple[int, int]]:
    """Crop `image_path` (the ORIGINAL, full-resolution rendered page) to the
    pixel region matching `box` (0..1000 coords of the full image), save the
    crop to `output_path`, and return:
    - the output path,
    - the pixel crop region actually used (needed later to remap coordinates
      the model returns relative to this crop back onto the full image),
    - the full image's own pixel size.
    """
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        width, height = image.size
        px_box = normalized_box_to_pixels(box, width, height, pad_pct=pad_pct)
        cropped = image.crop(px_box)
        cropped.save(output_path, format="PNG")
    return output_path, px_box, (width, height)


def remap_crop_box_to_full(
    crop_box_0_1000,
    crop_px_region: tuple[int, int, int, int],
    full_image_px_size: tuple[int, int],
) -> list[int]:
    """Convert a 0..1000 normalized box the model returned relative to a
    CROPPED image back into 0..1000 coordinates of the original full page
    image, so it can be merged with full-page detections, annotated, and
    stored using the same coordinate system as everything else."""
    cx0, cy0, cx1, cy1 = crop_px_region
    crop_w = max(1, cx1 - cx0)
    crop_h = max(1, cy1 - cy0)
    full_w, full_h = full_image_px_size

    left, top, right, bottom = _normalize_box(crop_box_0_1000)

    # crop-local pixel coords
    px_left = cx0 + (left / 1000.0) * crop_w
    px_top = cy0 + (top / 1000.0) * crop_h
    px_right = cx0 + (right / 1000.0) * crop_w
    px_bottom = cy0 + (bottom / 1000.0) * crop_h

    # back to full-image normalized 0..1000
    full_left = px_left / full_w * 1000.0
    full_top = px_top / full_h * 1000.0
    full_right = px_right / full_w * 1000.0
    full_bottom = px_bottom / full_h * 1000.0

    full_left = max(0, min(1000, int(round(full_left))))
    full_top = max(0, min(1000, int(round(full_top))))
    full_right = max(0, min(1000, int(round(full_right))))
    full_bottom = max(0, min(1000, int(round(full_bottom))))
    if full_right <= full_left:
        full_right = full_left + 1
    if full_bottom <= full_top:
        full_bottom = full_top + 1
    return [full_left, full_top, full_right, full_bottom]