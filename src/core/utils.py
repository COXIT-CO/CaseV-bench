import json
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw

from core.models.results import LabeledBox, LocationDetection

# Long-edge (px) each page image is downsampled to at ingest; snapshotted on a Run.
DEFAULT_DOWNSAMPLE_PX = 1568

# Colour the model's predicted boxes are drawn in on the prediction overlay (ticket 09).
# ``red`` reads clearly on a white drawing.
PREDICTION_COLOR = "red"


def parse_json(content: str) -> dict:
    def _strip_code_fence(content: str) -> str:
        if content is None:
            raise ValueError(
                "response content is empty (likely truncated before completion)"
            )
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        return content.strip()

    stripped = _strip_code_fence(content)
    return json.loads(stripped)


def downsample(
    source: Path, dest: Path, max_long_edge: int = DEFAULT_DOWNSAMPLE_PX
) -> Path:
    with Image.open(source) as image:
        scale = max_long_edge / max(image.size)
        new_size = (round(image.width * scale), round(image.height * scale))
        image.resize(new_size).save(dest)
    return dest


def _detections_to_boxes(
    detections: Iterable[LocationDetection],
) -> list[LabeledBox]:
    """Flatten model detections into the shared ``LabeledBox`` shape."""
    return [
        LabeledBox(
            d.label,
            d.bounding_box.x_min,
            d.bounding_box.y_min,
            d.bounding_box.x_max,
            d.bounding_box.y_max,
        )
        for d in detections
    ]


def _build_overlay(
    image_path: Path, groups: list[tuple[Iterable[LabeledBox], str]]
) -> Image.Image:
    """Draw one or more color-coded groups of labeled boxes onto a copy of the page image.

    ``groups`` is a list of ``(boxes, color)``; boxes later in the list are drawn on top.
    The single place box drawing lives, so the prediction overlay (ticket 09) has one
    canonical renderer."""
    with Image.open(image_path) as image:
        overlay = image.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    width, height = overlay.size
    for boxes, color in groups:
        for box in boxes:
            x0, y0 = box.x_min * width, box.y_min * height
            draw.rectangle(
                (x0, y0, box.x_max * width, box.y_max * height),
                outline=color,
                width=3,
            )
            # Label the box so the taxonomy class is legible on the overlay, not just
            # the location — a developer needs to tell a cabinet box from a countertop.
            draw.text((x0 + 2, max(0, y0 - 12)), box.label, fill=color)
    return overlay


def draw_overlay(
    image_path: Path, detections: list[LocationDetection], dest: Path
) -> Path:
    """Render the model's detections (red) on the page image and cache it to ``dest``
    (ticket 09)."""
    overlay = _build_overlay(
        image_path, [(_detections_to_boxes(detections), PREDICTION_COLOR)]
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(dest)
    return dest
