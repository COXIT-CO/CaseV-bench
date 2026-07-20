from __future__ import annotations

import os
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Separate colors make dense architectural sheets easier to inspect.
LABEL_COLORS: dict[str, tuple[int, int, int]] = {
    "elevation": (76, 201, 240),
    "cabinet": (72, 219, 155),
    "countertop": (255, 184, 77),
    "elevation_callout": (255, 99, 132),
}
DEFAULT_COLOR = (166, 133, 255)
LABEL_TEXT = (8, 12, 24)


def _normalized_box_to_pixels(
    box: Any,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    """Convert the prompt's 0..1000 coordinates to clamped pixel coordinates."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None

    try:
        left, top, right, bottom = (float(value) for value in box)
    except (TypeError, ValueError):
        return None

    # Models occasionally return a box with left/right or top/bottom swapped
    # even though the detection itself is valid. Sort instead of discarding
    # the box outright, so a correct detection isn't silently dropped from
    # the annotated image (kept consistent with result_parser.py).
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top

    # The localization prompt explicitly requires normalized page coordinates.
    left = max(0.0, min(1000.0, left))
    top = max(0.0, min(1000.0, top))
    right = max(0.0, min(1000.0, right))
    bottom = max(0.0, min(1000.0, bottom))

    if left >= right or top >= bottom:
        return None

    x0 = round(left / 1000.0 * width)
    y0 = round(top / 1000.0 * height)
    x1 = round(right / 1000.0 * width)
    y1 = round(bottom / 1000.0 * height)

    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 1, min(width - 1, x1))
    y1 = max(y0 + 1, min(height - 1, y1))
    return x0, y0, x1, y1


def annotate_image(image_path: str, objects: list[dict], output_path: str) -> str:
    """Draw all valid model detections on one rendered sheet image."""
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    line_width = max(3, round(min(width, height) / 650))
    label_padding = max(3, line_width)

    for object_index, obj in enumerate(objects, start=1):
        label = str(obj.get("label", "")).strip()
        pixel_box = _normalized_box_to_pixels(obj.get("box"), width, height)
        if not label or pixel_box is None:
            continue

        x0, y0, x1, y1 = pixel_box
        color = LABEL_COLORS.get(label, DEFAULT_COLOR)
        draw.rectangle(pixel_box, outline=color, width=line_width)

        # The index helps match a box on the image with the corresponding JSON item.
        caption = f"{object_index}. {label}"
        text_box = draw.textbbox((0, 0), caption, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        label_width = text_width + label_padding * 2
        label_height = text_height + label_padding * 2

        label_left = x0
        label_top = y0 - label_height
        if label_top < 0:
            label_top = y0
        if label_left + label_width > width:
            label_left = max(0, width - label_width)

        draw.rectangle(
            [label_left, label_top, label_left + label_width, label_top + label_height],
            fill=color,
        )
        draw.text(
            (label_left + label_padding, label_top + label_padding),
            caption,
            fill=LABEL_TEXT,
            font=font,
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    image.save(output_path, format="PNG")
    return output_path