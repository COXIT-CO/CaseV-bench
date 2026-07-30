"""Crop-shape-agnostic image resize + coordinate-remap helpers.

Shared by both Cutting mode (services/tiling.py, a page split into an
overlapping grid) and AI-crop mode (services/ai_crop.py, a page split into
whatever tagged regions Pass 1 finds) -- both features send several crops
of one page in a single combined LLM request and need to convert each
detection's crop-local coordinates back to full-image coordinates
afterward. The math is identical regardless of where the crop's box came
from (a grid cell vs. a detail-tag region), so it lives here once instead
of twice.
"""
from __future__ import annotations

import io

from PIL import Image

# Anthropic's own vision endpoint silently auto-resizes any image whose long
# edge exceeds this before it ever reaches the model -- a crop larger than
# this is resized ourselves first, for predictable, inspectable quality
# rather than leaving it to an opaque provider-side resize. Applied
# universally (Claude, GPT, Gemini, Qwen, GLM -- whichever model is
# selected, all routed through the same OpenRouter adapter) rather than
# conditionally on the chosen model: it's a downscale-ONLY cap (a smaller
# crop is never upscaled -- see crop_and_resize), so for any vendor whose
# own limit is >= this value it's a harmless no-op, and for any vendor with
# a *tighter* limit it still helps avoid an unrelated "image too large"
# rejection. Do not make this model-conditional.
CROP_MAX_EDGE = 1568


def crop_and_resize(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[bytes, float]:
    """Crop `box` (x0, y0, x1, y1) out of `image`, downscale it (preserving
    aspect ratio) if its long edge exceeds CROP_MAX_EDGE, and return (PNG
    bytes, scale factor applied -- 1.0 if none). Downscale-only: a crop
    already <= CROP_MAX_EDGE is never upscaled, since upscaling would
    fabricate detail the model could mistake for real.
    """
    crop = image.crop(box)
    long_edge = max(crop.width, crop.height)
    scale = min(CROP_MAX_EDGE / long_edge, 1.0) if long_edge else 1.0

    if scale < 1.0:
        new_size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
        crop = crop.convert("RGB").resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return buffer.getvalue(), scale


def remap_normalized_box(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    box_x0: int,
    box_y0: int,
    box_x1: int,
    box_y1: int,
    full_width: int,
    full_height: int,
) -> tuple[float, float, float, float]:
    """Convert a 0-1000-normalized box in a crop's local coordinate space
    (box_x0, box_y0, box_x1, box_y1 -- the crop's own pixel box in the FULL
    image, BEFORE any resize) back into 0-1000-normalized coordinates
    relative to the full image -- the convention every downstream consumer
    (BBoxCanvas.jsx, ResultsSummary.jsx, parser.build_location_export)
    already expects, with zero awareness of crops/tiles/cells.

    Because the model's bounding box output is normalized 0-1000 relative to
    whatever image it actually saw (the resized crop), and resize preserves
    aspect ratio, the resize scale factor cancels out algebraically here --
    only the crop's own offset (box_x0, box_y0) and its pre-resize size
    (box_x1-box_x0, box_y1-box_y0) matter. Callers don't need to pass a
    scale factor into this function at all.

    Results are clamped to [0, 1000] to absorb float rounding noise at crop
    edges (DetectedObject's Field bounds would otherwise reject an
    otherwise-valid detection over a hair of drift).
    """
    box_w = box_x1 - box_x0
    box_h = box_y1 - box_y0

    full_x_min = box_x0 + (x_min / 1000) * box_w
    full_y_min = box_y0 + (y_min / 1000) * box_h
    full_x_max = box_x0 + (x_max / 1000) * box_w
    full_y_max = box_y0 + (y_max / 1000) * box_h

    def _clamp(value: float, dimension: int) -> float:
        return max(0.0, min(1000.0, (value / dimension) * 1000))

    return (
        _clamp(full_x_min, full_width),
        _clamp(full_y_min, full_height),
        _clamp(full_x_max, full_width),
        _clamp(full_y_max, full_height),
    )
