"""Pre-flight image sizing, used for two independent oversized-image problems.

Page PNGs are rendered at whatever DPI the user chose (see pdf_processor.py)
and can end up huge for large-format architectural sheets -- a 42x30in sheet
at 300 DPI is 12600x9000px (113 megapixels). That's too big for two unrelated
consumers:

1. Some vision models fail outright on images that large: Qwen2.5-VL via
   OpenRouter returned "The source image cannot be decoded" on exactly such
   a page (used in `analyze.py` before the outgoing LLM API call).
2. Browsers can fail to decode it too -- `HTMLImageElement.decode()` in
   `BBoxCanvas.jsx` rejects with the *identical* message, "The source image
   cannot be decoded", for the same reason (too many pixels to decode/paint
   reliably) but for a totally different piece of the pipeline. This was
   initially misdiagnosed as an OpenRouter/model problem, since the wording
   is indistinguishable, before realizing the browser was the one failing to
   decode the full-resolution original fetched for the preview canvas (used
   in `pdf_processor.py` to precompute a display-safe copy at upload time).

Neither use touches the full-resolution on-disk original used for
`page.width`/`page.height` (the export-boundary pixel math in
`parser.build_location_export` and `ResultsSummary.jsx` depend on those being
the true PDF-render resolution, not whatever's convenient to decode) -- and
since a model's/canvas's `box` coordinates are normalized 0-1000 relative to
whatever image it actually received, shrinking either downstream copy doesn't
change that normalization or any rescaling math built on it.
"""
from __future__ import annotations

import io

from PIL import Image

# Generous enough to preserve fine drawing detail, well under every current
# provider's known limits (Qwen2.5-VL is the tightest we've hit in practice)
# and comfortably within any browser's image-decode budget.
MAX_DIMENSION = 4096
MAX_PIXELS = 8_000_000

# The rendered PNG is already-trusted output of our own PyMuPDF render step
# (not an arbitrary untrusted upload), so allow Pillow to open larger images
# than its conservative default without just warning/erroring.
Image.MAX_IMAGE_PIXELS = 300_000_000


def downscale_image(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Shrink an image if it's oversized, for either sending to a model or
    serving to a browser for the preview canvas.

    Returns (possibly-resized bytes, possibly-updated media type). Passes
    through unchanged when the image is already within bounds.
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        width, height = img.size
        longest_edge = max(width, height)
        pixel_count = width * height

        scale = min(
            MAX_DIMENSION / longest_edge,
            (MAX_PIXELS / pixel_count) ** 0.5,
            1.0,
        )
        if scale >= 1.0:
            return image_bytes, media_type

        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = img.convert("RGB").resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")
        return buffer.getvalue(), "image/png"
